import os
import re
import logging
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from services import index_service
from services import tracking_service

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("CODETRACE_CACHE", "/tmp/codetrace"))
_CLONE_THRESHOLD_DEFAULT_KB = 1024 * 1024  # 1GB
_SIZE_CACHE: dict[str, tuple[int | None, float]] = {}
_SIZE_CACHE_TTL = 3600  # 秒
_GIT_GRAPH_CACHE: dict[str, tuple[float, dict]] = {}
_GIT_GRAPH_TTL = 300  # 秒，Dashboard 重复打开/刷新免重复计算

# 内存缓存：repo_url → (repo_path, timestamp)
_cache = {}
_CACHE_TTL = 60  # 秒，避免跨请求读到过时数据

# ── 并发锁 ──────────────────────────────────────────
# 用 mkdir 原子性做锁（跨平台，不需要第三方库）
_LOCK_DIR = CACHE_DIR / ".locks"


def _acquire_lock(name: str, timeout: float = 30) -> bool:
    """获取 repo 级别锁。返回是否成功。"""
    try:
        os.makedirs(_LOCK_DIR, exist_ok=True)
    except Exception as e:
        logger.warning("锁目录创建失败 %s: %s", _LOCK_DIR, e)
        return False
    lock_path = _LOCK_DIR / name.replace("/", "_").replace(":", "_")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.mkdir(lock_path)
            return True
        except FileExistsError:
            time.sleep(0.2)
    logger.warning("获取仓库锁超时 name=%s", name)
    return False


def _release_lock(name: str):
    lock_path = _LOCK_DIR / name.replace("/", "_").replace(":", "_")
    try:
        os.rmdir(lock_path)
    except OSError:
        pass


# ── 缓存清理 ────────────────────────────────────────
_LAST_CLEANUP = 0


def _maybe_cleanup():
    """每 10 分钟检查一次，删除 7 天未访问的缓存 repo。"""
    global _LAST_CLEANUP
    now = time.time()
    if now - _LAST_CLEANUP < 600:
        return
    _LAST_CLEANUP = now

    if not CACHE_DIR.exists():
        return

    for entry in os.scandir(CACHE_DIR):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            # 检查 .git/HEAD 的最后修改时间
            git_head = Path(entry.path) / ".git" / "HEAD"
            if not git_head.exists():
                continue
            atime = git_head.stat().st_atime
            if now - atime > 7 * 86400:
                import shutil
                shutil.rmtree(entry.path, ignore_errors=True)
        except Exception:
            pass


def _remote_head(repo_url: str) -> str | None:
    """获取远程仓库 HEAD 的 commit hash。返回 None 表示远程不可达。"""
    try:
        result = _run_git_with_proxy_fallback(
            ["ls-remote", repo_url, "HEAD"], timeout=8, retry_on_timeout=False,
            repo_url=repo_url,
        )
        return result.stdout.split()[0] if result.stdout.strip() else None
    except Exception as e:
        logger.warning("git ls-remote 失败 repo=%s: %s", repo_url, e)
        return None


def _github_auth_header() -> str | None:
    """GitHub Token 转 Basic 认证头（x-access-token），用于私有仓库 clone/pull。"""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        return None
    import base64
    raw = f"x-access-token:{token}"
    return "Authorization: Basic " + base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _git_auth_env(repo_url: str | None) -> dict[str, str] | None:
    """
    GitHub https 仓库带 GITHUB_TOKEN 时的认证配置（环境变量注入，避免出现在进程参数里）。
    非 GitHub / SSH / 无 token 返回 None。
    """
    auth = _github_auth_header()
    if auth and _is_github_https(repo_url):
        base_count = int(os.environ.get("GIT_CONFIG_COUNT", "0") or "0")
        return {
            "GIT_CONFIG_COUNT": str(base_count + 1),
            f"GIT_CONFIG_KEY_{base_count}": "http.extraHeader",
            f"GIT_CONFIG_VALUE_{base_count}": auth,
        }
    return None


def _is_github_https(repo_url: str | None) -> bool:
    """精确判断是否为 GitHub 官方主机的 https URL（防 github.com.evil.com / userinfo 注入）。"""
    if not repo_url or not repo_url.startswith("https://"):
        return False
    host = (urlparse(repo_url).hostname or "").lower()
    return host in ("github.com", "www.github.com")


def _git_net_args(extra_args: list[str]) -> list[str]:
    """
    git 网络命令参数：CODETRACE_GIT_PROXY 显式代理优先，否则继承用户 git 配置；
    """
    proxy = os.getenv("CODETRACE_GIT_PROXY", "").strip()
    args = []
    if proxy:
        args += ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    return ["git"] + args + extra_args


def _run_git_with_proxy_fallback(
    extra_args: list[str], cwd=None, timeout: int = 120, retry_on_timeout: bool = True,
    repo_url: str | None = None,
) -> subprocess.CompletedProcess:
    """
    运行 git 网络命令（兼容不同用户的代理环境）：
    1. 先按配置执行（显式代理或用户 git 配置中的代理）；
    2. 失败后清空代理直连重试一次（覆盖代理配置错误/代理未运行但可直连的场景）。
    """
    first = _git_net_args(extra_args)
    auth_env = _git_auth_env(repo_url)
    env = {**os.environ, **auth_env} if auth_env else None
    timed_out = False
    try:
        r = subprocess.run(
            first, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout,
        )
        if r.returncode == 0:
            return r
    except subprocess.TimeoutExpired:
        timed_out = True
    except Exception:
        pass
    if timed_out and not retry_on_timeout:
        # 首轮已超时且调用方不要求重试（如 ls-remote）：直接抛，避免代理黑洞翻倍延迟
        raise subprocess.TimeoutExpired(first, timeout=timeout)
    direct = ["git", "-c", "http.proxy=", "-c", "https.proxy="] + extra_args
    r2 = subprocess.run(
        direct, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=timeout,
    )
    if r2.returncode != 0:
        raise subprocess.CalledProcessError(
            r2.returncode, direct, output=r2.stdout, stderr=r2.stderr
        )
    return r2


def _request_index_background(repo_path: Path):
    """后台异步补索引（不阻塞请求路径；失败静默，查询走 git 回退）。"""
    try:
        if index_service.index_fresh(repo_path):
            return  # 已新鲜，避免每个请求都开线程
        index_service.request_index_build(repo_path)
    except Exception:
        pass


def repo_path_for_url(repo_url: str) -> Path:
    """从 URL 推导本地缓存路径（不 clone、不触网）。"""
    return CACHE_DIR / repo_name_for_url(repo_url)


def repo_name_for_url(repo_url: str) -> str:
    """
    从 URL 提取缓存目录名：主机+路径（含 owner），不同 owner 同名仓库不再冲突。
    防目录穿越（'..'/'.'/特殊字符清洗），过长时哈希截断。
    """
    try:
        parsed = urlparse(repo_url)
        host = (parsed.hostname or "git").lower()
        if not parsed.scheme and "@" in repo_url and ":" in repo_url:
            # SSH 形式：git@github.com:owner/repo.git
            userhost, _, ssh_path = repo_url.partition(":")
            host = userhost.rsplit("@", 1)[-1].lower()
            path = ssh_path.strip("/").removesuffix(".git").rstrip("/")
        else:
            path = parsed.path.strip("/").removesuffix(".git").rstrip("/")
        raw = f"{host}_{path}" if path else host
    except Exception:
        raw = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)
    if name in ("", ".", ".."):
        name = "_"
    if len(name) > 80:
        import hashlib
        name = name[:40] + "_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:12]
    return name


def repo_full_from_url(repo_url: str) -> str | None:
    """从 https/SSH URL 提取 owner/repo（GitHub API 用），失败返回 None。"""
    try:
        if "@" in repo_url and ":" in repo_url and not urlparse(repo_url).scheme:
            # SSH 形式：git@github.com:owner/repo.git
            path = repo_url.partition(":")[2].strip("/").removesuffix(".git")
        else:
            path = urlparse(repo_url).path.strip("/").removesuffix(".git")
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    except Exception:
        pass
    return None


def _legacy_cache_path(repo_url: str) -> Path:
    """旧版缓存目录名（仅 URL 末段），用于一次性迁移。"""
    raw = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)
    return CACHE_DIR / (name if name not in ("", ".", "..") else "_")


def _norm_url(url: str) -> str:
    return url.rstrip("/").removesuffix(".git").lower()


def _migrate_legacy_cache(repo_url: str, repo_path: Path) -> bool:
    """旧版同名缓存迁移到新命名（仅当 origin 与 URL 匹配），含索引/状态文件。"""
    legacy = _legacy_cache_path(repo_url)
    if repo_path.exists() or not legacy.exists() or not (legacy / ".git").exists():
        return False
    try:
        origin = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=legacy, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if not origin or _norm_url(origin) != _norm_url(repo_url):
            return False
        os.rename(legacy, repo_path)
        index_service.rename_index_for(legacy, repo_path)
        return True
    except Exception:
        return False


def _repo_size_kb(repo_url: str) -> int | None:
    """GitHub API 查仓库体积（KB）。非 GitHub 或查询失败返回 None。"""
    try:
        parsed = urlparse(repo_url)
        if parsed.hostname != "github.com":
            return None
        cached = _SIZE_CACHE.get(repo_url)
        if cached and time.time() - cached[1] < _SIZE_CACHE_TTL:
            return cached[0]
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) < 2 or not path_parts[0] or not path_parts[1]:
            return None
        owner, name = path_parts[0], path_parts[1].replace(".git", "")
        headers = {"Accept": "application/vnd.github+json"}
        token = os.getenv("GITHUB_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        proxy = os.getenv("CODETRACE_GIT_PROXY", "").strip()
        if proxy:
            with httpx.Client(proxy=proxy) as client:
                resp = client.get(
                    f"https://api.github.com/repos/{owner}/{name}",
                    headers=headers, timeout=10,
                )
        else:
            resp = httpx.get(
                f"https://api.github.com/repos/{owner}/{name}",
                headers=headers, timeout=10,
            )
        resp.raise_for_status()
        size = resp.json().get("size")
        size_kb = int(size) if size else None
        _SIZE_CACHE[repo_url] = (size_kb, time.time())
        return size_kb
    except Exception:
        return None


def _should_full_clone(repo_url: str) -> bool:
    """体积 ≤ 阈值 → 全量 clone（完整历史，索引价值更高）；否则维持浅克隆。"""
    try:
        threshold_kb = int(
            os.getenv("CODETRACE_CLONE_THRESHOLD_KB", str(_CLONE_THRESHOLD_DEFAULT_KB))
        )
    except ValueError:
        threshold_kb = _CLONE_THRESHOLD_DEFAULT_KB
    size_kb = _repo_size_kb(repo_url)
    return size_kb is not None and size_kb <= threshold_kb


def _is_shallow_repo(repo_path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def clone_or_pull_repo(repo_url: str) -> Path:
    """
    Clone the repository if it doesn't exist, or pull if the remote has changed.

    Uses `git ls-remote HEAD` to check if the remote has advanced
    before pulling, avoiding unnecessary git pull calls.

    Args:
        repo_url (str): The URL of the Git repository.

    Returns:
        Path: The path to the cloned or updated repository.
    """
    repo_name = repo_name_for_url(repo_url)
    repo_path = repo_path_for_url(repo_url)

    # 内存缓存命中（60s TTL），避免重复网络请求
    cached = _cache.get(repo_url)
    if cached:
        entry_path, entry_time = cached
        if time.time() - entry_time < _CACHE_TTL:
            return entry_path
        del _cache[repo_url]

    # 定期清理过期缓存
    _maybe_cleanup()

    # ── 不存在 → clone ──
    if not repo_path.exists():
        locked = _acquire_lock(repo_name)
        try:
            if not repo_path.exists():
                _migrate_legacy_cache(repo_url, repo_path)
            if not repo_path.exists():
                full_clone = _should_full_clone(repo_url)
                clone_args = ["clone"]
                if not full_clone:
                    clone_args.append("--depth=500")
                clone_args += [repo_url, str(repo_path)]
                try:
                    _run_git_with_proxy_fallback(
                        clone_args, timeout=600 if full_clone else 120, repo_url=repo_url,
                    )
                except subprocess.TimeoutExpired:
                    # 超时：清掉半成品，降级浅克隆
                    import shutil
                    if repo_path.parent == CACHE_DIR:
                        shutil.rmtree(repo_path, ignore_errors=True)
                    _run_git_with_proxy_fallback(
                        ["clone", "--depth=500", repo_url, str(repo_path)],
                        timeout=600, repo_url=repo_url,
                    )
        finally:
            if locked:
                _release_lock(repo_name)
        _request_index_background(repo_path)
        invalidate_git_graph_cache(repo_path)
        tracking_service.request_tracking(repo_path)
        _cache[repo_url] = (repo_path, time.time())
        return repo_path

    # ── 已存在 → 缓存并检查远程是否有新 commit ──
    _cache[repo_url] = (repo_path, time.time())
    remote_hash = _remote_head(repo_url)
    if remote_hash is None:
        _request_index_background(repo_path)
        return repo_path

    try:
        local_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        local_hash = local_result.stdout.strip()
    except Exception:
        local_hash = ""

    if local_hash == remote_hash:
        _request_index_background(repo_path)
        return repo_path

    # 不一致 → pull（加锁防并发）
    locked = _acquire_lock(repo_name)
    try:
        pull_args = ["pull"]
        if _is_shallow_repo(repo_path):
            pull_args.append("--depth=500")
        _run_git_with_proxy_fallback(
            pull_args, cwd=repo_path, timeout=300, repo_url=repo_url,
        )
        invalidate_git_graph_cache(repo_path)
        tracking_service.request_tracking(repo_path)
    except Exception as e:
        logger.warning("git pull 失败 repo=%s: %s", repo_path, e)
    finally:
        if locked:
            _release_lock(repo_name)

    _request_index_background(repo_path)
    return repo_path

def get_file_commits(repo_path: Path, file_path: str) -> list[dict]:
    """
    Get the commit history for a specific file in the repository.

    Args:
        repo_path (Path): The path to the cloned repository.
        file_path (str): The relative path to the file within the repository.

    Returns:
        list[dict]: A list of commit info dicts with keys: hash, author, date, message.
    """
    # 索引优先：新鲜则走 SQLite，异常回退 git
    if index_service.index_fresh(repo_path):
        try:
            return index_service.get_file_commits(repo_path, file_path)
        except Exception:
            pass

    # Use git log to get the commit history for the specific file
    result = subprocess.run(
        ["git", "log", "--follow", "--format=%H|%an|%ai|%s", "--", file_path],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30
    )

    # Parse the output into structured dicts
    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 3)
        commits.append({
            "hash": parts[0],
            "author": parts[1],
            "date": parts[2],
            "message": parts[3],
        })
    return commits


def get_commit_diff_stats(repo_path: Path, commit_hash: str) -> dict:
    """
    获取指定提交的差异统计信息，包括新增行数、删除行数和修改行数。

    Args:
        repo_path (Path): 仓库的路径。
        commit_hash (str): 提交的哈希值。
    
    Returns:
        dict: {"additions": int, "deletions": int, "files_changed": int}
    """
    if index_service.index_fresh(repo_path):
        try:
            return index_service.get_commit_diff_stats(repo_path, commit_hash)
        except Exception:
            pass

    result = subprocess.run(
        ["git", "show", "--stat", "--format=", commit_hash],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )

    additions = 0
    deletions = 0
    files_changed = 0

    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        files_changed += 1
        # 解析 "1 file changed, 2 insertions(+), 1 deletion(-)" 这种格式
        if "insertion" in line or "deletion" in line:
            # 简单按逗号分割，然后提取数字
            for part in line.split(","):
                part = part.strip()
                if "insertion" in part:
                    additions += int(part.split()[0])
                elif "deletion" in part:
                    deletions += int(part.split()[0])

    return {
        "additions": additions,
        "deletions": deletions,
        "files_changed": files_changed,
    }

def get_commit_diff_content(repo_path: Path, commit_hash: str, max_size: int = 15000) -> dict:
    """
    获取指定提交的完整 diff 文本（patch）。

    Args:
        repo_path: 仓库路径。
        commit_hash: 提交哈希。
        max_size: diff 文本最大长度，超长截断。

    Returns:
        dict: {"patch": str, "files_changed": [str], "additions": int, "deletions": int}
    """
    result = subprocess.run(
        ["git", "show", "--format=", commit_hash],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )

    patch = result.stdout.strip()
    truncated = len(patch) > max_size
    if truncated:
        patch = patch[:max_size] + "\n... (diff 过长已截断)"

    # 变更文件列表
    files = []
    for line in patch.split("\n"):
        if line.startswith("diff --git"):
            parts = line.split()
            if len(parts) >= 3:
                files.append(parts[2].removeprefix("a/"))

    # 通过 --stat 拿精确统计
    stat = get_commit_diff_stats(repo_path, commit_hash)

    return {
        "patch": patch,
        "files_changed": files,
        "additions": stat["additions"],
        "deletions": stat["deletions"],
        "truncated": truncated,
    }

def get_file_content_at_commit(repo_path: Path, commit_hash: str, file_path: str) -> str:
    """
    获取指定提交中某个文件的内容。

    Args:
        repo_path (Path): 仓库的路径。
        commit_hash (str): 提交的哈希值。
        file_path (str): 文件在仓库中的相对路径。
    
    Returns:
        str: 文件内容的字符串。
    """
    result = subprocess.run(
        ["git", "show", f"{commit_hash}:{file_path}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=15,
    )
    return result.stdout


def list_files_at_commit(repo_path: Path, commit_hash: str) -> list[str]:
    """获取指定 commit 的仓库文件列表"""
    if commit_hash == "HEAD" and index_service.index_fresh(repo_path):
        try:
            return index_service.list_files_at_head(repo_path)
        except Exception:
            pass

    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit_hash],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8",
        check=True, timeout=30,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


def list_files_changed_in_commit(repo_path: Path, commit_hash: str) -> list[str]:
    """获取指定 commit 中变更的文件列表（缩小搜索范围用）"""
    result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", commit_hash],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8",
        check=True, timeout=15,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


def get_top_changed_files(repo_path: Path, top_n: int = 10) -> list[str]:
      """扫描整个仓库，返回变更频率最高的前 N 个文件路径。"""
      if index_service.index_fresh(repo_path):
          try:
              return index_service.get_top_changed_files(repo_path, top_n)
          except Exception:
              pass

      from collections import Counter

      result = subprocess.run(
          ["git", "log", "--pretty=format:", "--name-only"],
          cwd=repo_path,
          capture_output=True,
          text=True,
          encoding="utf-8",
          timeout=30,
      )
      counter = Counter()
      for line in result.stdout.strip().split("\n"):
          line = line.strip()
          if line:
              counter[line] += 1
      return [f for f, _ in counter.most_common(top_n)]

def get_file_commit_counts(repo_path: Path) -> dict:
    """轻量：获取每个文件的 commit 次数，用于风险评估。"""
    if index_service.index_fresh(repo_path):
        try:
            return index_service.get_file_commit_counts(repo_path)
        except Exception:
            pass

    from collections import Counter

    result = subprocess.run(
        ["git", "log", "--pretty=format:", "--name-only"],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    counter = Counter()
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line:
            counter[line] += 1
    return dict(counter)


def get_repo_summary(repo_path: Path) -> dict:
    """快速聚合仓库概要统计。"""
    if index_service.index_fresh(repo_path):
        try:
            return index_service.get_repo_summary(repo_path)
        except Exception:
            pass

    # Total authors + recent commits in one pass
    log_result = subprocess.run(
        ["git", "log", "--pretty=format:%an|%ai|%s", "-50"],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    authors = set()
    recent = []
    for line in log_result.stdout.strip().split("\n"):
        if not line: continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            authors.add(parts[0])
            if len(recent) < 15:
                recent.append({"author": parts[0], "date": parts[1][:10], "message": parts[2][:80]})

    # Total commit count
    count_result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    total_commits = int(count_result.stdout.strip() or 0)

    # Total files
    files_result = subprocess.run(
        ["git", "ls-tree", "-r", "HEAD"],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    total_files = len([l for l in files_result.stdout.split("\n") if l.strip()])

    # Top changed files
    top = get_top_changed_files(repo_path, top_n=10)

    return {
        "total_commits": total_commits,
        "total_files": total_files,
        "total_authors": len(authors),
        "top_files": top,
        "recent_commits": recent,
    }


def get_repo_health_stats(repo_path: Path, top_n: int = 10) -> list[dict]:
    """旧版——保留向后兼容。推荐使用 get_file_health_stats。"""
    from collections import Counter

    result = subprocess.run(
        ["git", "log", "--pretty=format:%s", "--name-only"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )

    lines = result.stdout.strip().split("\n")
    file_counter = Counter()
    bug_counter = Counter()
    current_message = ""

    for line in lines:
        line = line.strip()
        if not line:
            current_message = ""
            continue
        if not line.startswith("backend/") and not line.startswith("frontend/") and not line.startswith("extension/"):
            current_message = line
            continue
        file_counter[line] += 1
        msg_lower = current_message.lower()
        if any(kw in msg_lower for kw in ("fix", "bug", "hotfix", "patch", "修复", "resolve", "issue")):
            bug_counter[line] += 1

    stats = []
    for filepath, total in file_counter.most_common(top_n):
        bugs = bug_counter.get(filepath, 0)
        stats.append({
            "file": filepath,
            "total_changes": total,
            "bug_fixes": bugs,
            "bug_ratio": round(bugs / total, 2) if total > 0 else 0,
        })
    return stats


def get_file_health_stats(repo_path: Path, top_n: int = 20) -> list[dict]:
    """
    升级版健康度检测：使用 git log --numstat 获取真实 churn 数据，
    移除目录硬编码过滤，加入时效加权。

    返回每个文件的：
    - file: 文件路径
    - total_commits: 总提交次数
    - total_additions: 总新增行数
    - total_deletions: 总删除行数
    - churn: 总变更行数 (additions + deletions)
    - recency_score: 时效分数（值越大表示近期越活跃）
    - commit_messages: 最近 commit message 列表（供 LLM 语义分类）
    - top_authors: 主要贡献者
    """
    if index_service.index_fresh(repo_path):
        try:
            return index_service.get_file_health_stats(repo_path, top_n)
        except Exception:
            pass

    from collections import defaultdict
    import datetime

    # git log --numstat 输出格式：
    # additions\tdeletions\tfilepath
    # 每个 commit 前有一行 commit-head 信息
    result = subprocess.run(
        ["git", "log", "--numstat", "--pretty=format:__COMMIT__%H|%an|%ai|%s", "--reverse"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    file_data = defaultdict(lambda: {
        "commits": 0, "additions": 0, "deletions": 0,
        "messages": [], "authors": set(), "dates": [],
    })

    current_commit = None
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        if line.startswith("__COMMIT__"):
            # 新 commit 开始
            parts = line[10:].split("|", 3)
            current_commit = {
                "hash": parts[0] if len(parts) > 0 else "",
                "author": parts[1] if len(parts) > 1 else "",
                "date": parts[2] if len(parts) > 2 else "",
                "message": parts[3] if len(parts) > 3 else "",
                "seen_files": set(),
            }
        elif current_commit and "\t" in line:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            add_str, del_str, filepath = parts
            if add_str == "-" or del_str == "-":
                continue  # 二进制文件

            try:
                add_val = int(add_str) if add_str != "" else 0
                del_val = int(del_str) if del_str != "" else 0
            except ValueError:
                continue

            fd = file_data[filepath]
            fd["commits"] += 1
            fd["additions"] += add_val
            fd["deletions"] += del_val
            fd["authors"].add(current_commit["author"])
            fd["dates"].append(current_commit["date"])
            if current_commit["message"] and len(fd["messages"]) < 5:
                fd["messages"].append(current_commit["message"])

    now = datetime.datetime.now()
    stats = []
    for filepath, fd in file_data.items():
        if fd["commits"] == 0:
            continue

        # 时效加权：最近 30 天内的 commit 权重大
        recency_score = 0
        for d in fd.get("dates", []):
            try:
                dt = datetime.datetime.strptime(d[:10], "%Y-%m-%d")
                days_ago = (now - dt).days
                if days_ago <= 7:
                    recency_score += 10
                elif days_ago <= 30:
                    recency_score += 5
                elif days_ago <= 90:
                    recency_score += 2
                else:
                    recency_score += 0.5
            except Exception:
                recency_score += 0.5

        stats.append({
            "file": filepath,
            "total_commits": fd["commits"],
            "total_additions": fd["additions"],
            "total_deletions": fd["deletions"],
            "churn": fd["additions"] + fd["deletions"],
            "recency_score": round(recency_score, 1),
            "commit_messages": list(fd["messages"]),
            "top_authors": list(fd["authors"])[:3],
        })

    # 按 churn + recency 综合排序
    stats.sort(key=lambda x: x["churn"] * x["recency_score"], reverse=True)
    return stats[:top_n]

def get_file_bulk_summary(repo_path: Path, file_paths: list[str]) -> list[dict]:
    """
    批量获取多个文件的基本信息：每个文件的 commit 数量、最近修改日期、主要贡献者。
    用于 Agent 快速了解多个热点文件的活跃程度。
    """
    summaries = []
    for fp in file_paths:
        try:
            commits = get_file_commits(repo_path, fp)
            authors = list({c["author"] for c in commits[:20]})
            summaries.append({
                "file": fp,
                "total_commits": len(commits),
                "latest_date": commits[0]["date"] if commits else None,
                "top_authors": authors[:5],
            })
        except Exception:
            summaries.append({"file": fp, "error": "无法读取"})
    return summaries


def get_recent_commit_groups(repo_path: Path, count: int = 15) -> list[dict]:
    """
    获取最近 N 个 commit 的变更文件组和 diff 行数摘要。

    输出给 LLM 判断：哪些 commit 是跨文件重构事件（一次重构改了多个文件），
    哪些是独立变更。

    Returns:
        list[dict]: 每项含 commit_hash, author, date, message,
                    file_count, total_churn, files (列表，含 path, additions, deletions)
    """
    if index_service.index_fresh(repo_path):
        try:
            return index_service.get_recent_commit_groups(repo_path, count)
        except Exception:
            pass

    result = subprocess.run(
        ["git", "log", "--numstat", "--pretty=format:__COMMIT__%H|%an|%ai|%s", f"-{count}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    groups = []
    current = None
    current_files = []

    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("__COMMIT__"):
            if current:
                current["files"] = current_files
                current["file_count"] = len(current_files)
                current["total_churn"] = sum(f.get("additions", 0) + f.get("deletions", 0) for f in current_files)
                if current["file_count"] > 0:
                    groups.append(current)

            parts = line[10:].split("|", 3)
            current = {
                "commit_hash": parts[0] if len(parts) > 0 else "",
                "author": parts[1] if len(parts) > 1 else "",
                "date": parts[2] if len(parts) > 2 else "",
                "message": parts[3] if len(parts) > 3 else "",
            }
            current_files = []
        elif current and "\t" in line:
            parts = line.split("\t")
            if len(parts) == 3:
                add_s, del_s, path = parts
                if add_s != "-" and del_s != "-":
                    try:
                        current_files.append({
                            "path": path,
                            "additions": int(add_s) if add_s else 0,
                            "deletions": int(del_s) if del_s else 0,
                        })
                    except ValueError:
                        pass

    if current:
        current["files"] = current_files
        current["file_count"] = len(current_files)
        current["total_churn"] = sum(f.get("additions", 0) + f.get("deletions", 0) for f in current_files)
        if current["file_count"] > 0:
            groups.append(current)

    return groups


def get_co_change_trends(repo_path: Path, window_days: int = 30) -> list[dict]:
    """
    双窗口 co-change 趋势检测：比较近期与历史时期文件耦合关系的变化。

    分别对最近 window_days 天（近期窗口）和前一个 window_days 天（历史窗口）
    计算文件的 co-change 矩阵，然后对比两个窗口的指标变化。

    Args:
        repo_path: 仓库本地路径。
        window_days: 单个窗口天数，默认 30。

    Returns:
        list[dict]: 按风险排序的列表，每项含:
            - file: 文件路径
            - recent_partners: 近期窗口中共变伙伴数
            - old_partners: 历史窗口中共变伙伴数
            - coupling_growth: 伙伴数增长率
            - boundary_crossings: 近期跨模块共现次数
            - risk: 预置风险标签
    """
    if index_service.index_fresh(repo_path):
        try:
            return index_service.get_co_change_trends(repo_path, window_days)
        except Exception:
            pass

    from collections import defaultdict, Counter
    import datetime

    now = datetime.datetime.now()

    def window_commits(since_days: int, until_days: int) -> list[set[str]]:
        since = (now - datetime.timedelta(days=since_days)).strftime("%Y-%m-%d")
        until = (now - datetime.timedelta(days=until_days)).strftime("%Y-%m-%d")
        result = subprocess.run(
            ["git", "log", "--name-only", "--pretty=format:", "--since", since, "--until", until],
            cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        groups = []
        current_set = set()
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                if current_set:
                    groups.append(current_set)
                    current_set = set()
                continue
            current_set.add(line)
        if current_set:
            groups.append(current_set)
        return groups

    recent_groups = window_commits(window_days, 0)
    old_groups = window_commits(window_days * 2, window_days)

    def build_metrics(groups):
        partner_map = defaultdict(set)
        bx_counter = Counter()
        for files in groups:
            flist = list(files)
            for i, fa in enumerate(flist):
                for fb in flist[i + 1:]:
                    partner_map[fa].add(fb)
                    partner_map[fb].add(fa)
                    ma = fa.replace("\\", "/").split("/")[0] if "/" in fa or "\\" in fa else ""
                    mb = fb.replace("\\", "/").split("/")[0] if "/" in fb or "\\" in fb else ""
                    if ma and mb and ma != mb:
                        bx_counter[fa] += 1
                        bx_counter[fb] += 1
        return {f: {"partners": len(p), "bx": bx_counter.get(f, 0)} for f, p in partner_map.items()}

    recent_m = build_metrics(recent_groups)
    old_m = build_metrics(old_groups)

    all_files = set(recent_m.keys()) | set(old_m.keys())
    results = []
    for f in all_files:
        rec = recent_m.get(f, {"partners": 0, "bx": 0})
        old = old_m.get(f, {"partners": 0, "bx": 0})
        if rec["partners"] == 0 and old["partners"] == 0:
            continue
        old_p = old["partners"] or 1
        growth = round((rec["partners"] - old["partners"]) / old_p, 2)
        delta = rec["partners"] - old["partners"]
        rec_p = rec["partners"]
        results.append({
            "file": f,
            "recent_partners": rec["partners"],
            "old_partners": old["partners"],
            "coupling_growth": growth,
            "boundary_crossings": rec["bx"],
            "risk": "high" if rec_p >= 8 and delta >= 5 else ("medium" if rec_p >= 4 and delta >= 2 else "low"),
        })

    results.sort(key=lambda x: (-x["coupling_growth"], -x["boundary_crossings"]))
    return results[:30]


def get_co_change_edges(repo_path: Path, window_days: int = 30) -> dict:
    """
    双窗口 co-change 边数据，用于前端力导向图渲染。

    Args:
        repo_path: 仓库本地路径。
        window_days: 单个窗口天数，默认 30。

    Returns:
        dict: {nodes: [...], edges: [...]}
            nodes 每项含 id/label/module/recent_partners/old_partners/coupling_growth/boundary_crossings/risk
            edges 每项含 source/target/weight（weight = 共变次数）
    """
    if index_service.index_fresh(repo_path):
        try:
            return index_service.get_co_change_edges(repo_path, window_days)
        except Exception:
            pass

    from collections import defaultdict, Counter
    import datetime

    now = datetime.datetime.now()

    def window_commits(since_days: int, until_days: int) -> list[set[str]]:
        since = (now - datetime.timedelta(days=since_days)).strftime("%Y-%m-%d")
        until = (now - datetime.timedelta(days=until_days)).strftime("%Y-%m-%d")
        result = subprocess.run(
            ["git", "log", "--name-only", "--pretty=format:", "--since", since, "--until", until],
            cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        groups = []
        current_set = set()
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                if current_set:
                    groups.append(current_set)
                    current_set = set()
                continue
            current_set.add(line)
        if current_set:
            groups.append(current_set)
        return groups

    recent_groups = window_commits(window_days, 0)
    old_groups = window_commits(window_days * 2, window_days)

    # ── 统计 edges、partner 数、跨模块共现 ──
    edge_counter = Counter()          # {(fa, fb): weight}
    recent_map = defaultdict(set)     # {file: {partners}}
    bx_counter = Counter()            # {file: 跨模块共现次数}

    for files in recent_groups:
        flist = list(files)
        for i, fa in enumerate(flist):
            for fb in flist[i + 1:]:
                key = tuple(sorted([fa, fb]))
                edge_counter[key] += 1
                recent_map[fa].add(fb)
                recent_map[fb].add(fa)
                ma = fa.replace("\\", "/").split("/")[0] if "/" in fa or "\\" in fa else ""
                mb = fb.replace("\\", "/").split("/")[0] if "/" in fb or "\\" in fb else ""
                if ma and mb and ma != mb:
                    bx_counter[fa] += 1
                    bx_counter[fb] += 1

    # ── 旧窗口 partner 数 ──
    old_map = defaultdict(set)
    for files in old_groups:
        flist = list(files)
        for i, fa in enumerate(flist):
            for fb in flist[i + 1:]:
                old_map[fa].add(fb)
                old_map[fb].add(fa)

    # ── 构建 nodes ──
    all_files = set(recent_map.keys()) | set(old_map.keys())
    nodes = []
    for f in all_files:
        rec_p = len(recent_map.get(f, set()))
        old_p = len(old_map.get(f, set()))
        if rec_p == 0 and old_p == 0:
            continue
        denom = old_p or 1
        growth = round((rec_p - old_p) / denom, 2)
        module = f.replace("\\", "/").split("/")[0] if "/" in f or "\\" in f else ""
        nodes.append({
            "id": f,
            "label": f.split("/")[-1].split("\\")[-1],
            "module": module,
            "recent_partners": rec_p,
            "old_partners": old_p,
            "coupling_growth": growth,
            "boundary_crossings": bx_counter.get(f, 0),
            "risk": "high" if rec_p >= 8 and (rec_p - old_p) >= 5 else ("medium" if rec_p >= 4 and (rec_p - old_p) >= 2 else "low"),
        })

    # 按 coupling_growth 降序，取 top 30
    nodes.sort(key=lambda x: (-x["coupling_growth"], -x["boundary_crossings"]))
    top_nodes = nodes[:30]
    top_ids = {n["id"] for n in top_nodes}

    # ── 构建 edges（只包含 top 节点之间的边） ──
    edges = []
    for (fa, fb), weight in edge_counter.most_common(200):
        if fa in top_ids and fb in top_ids:
            edges.append({"source": fa, "target": fb, "weight": weight})

    return {"nodes": top_nodes, "edges": edges}


def get_file_change_context(repo_path: Path, file_path: str, count: int = 10) -> list[dict]:
    """
    获取某文件最近 N 个 commit 的变更上下文：message + diff 摘要。

    Args:
        repo_path: 仓库路径。
        file_path: 文件相对路径。
        count: 最近 commit 数，默认 10。

    Returns:
        list[dict]: 每项含 commit_hash, author, date, message, diff_summary, files_changed。
    """
    result = subprocess.run(
        ["git", "log", f"-{count}", "--format=%H|%an|%ai|%s", "--", file_path],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
    )

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue

        commit_hash = parts[0]
        author = parts[1]
        date = parts[2]
        message = parts[3]

        # 获取该 commit 的 diff 统计和详细摘要
        try:
            diff_result = subprocess.run(
                ["git", "diff", f"{commit_hash}~1", commit_hash, "--", file_path,
                 "--unified=3", "--no-color"],
                cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=15,
            )
            diff_text = diff_result.stdout
        except Exception:
            diff_text = ""

        # 精简 diff 到 1000 字符
        diff_short = diff_text[:1000]
        if len(diff_text) > 1000:
            diff_short += "\n... (diff truncated)"

        commits.append({
            "commit_hash": commit_hash[:8],
            "author": author,
            "date": date[:10],
            "message": message,
            "diff_summary": diff_short,
        })

    return commits


# ── Git Graph（Dashboard 分支拓扑 + 合入关系） ─────────────


def _default_branch(repo_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        name = result.stdout.strip()
        return name if name else "main"
    except Exception:
        return "main"


def _count_commits(repo_path: Path, ref: str) -> int:
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", ref],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        return int(result.stdout.strip() or 0)
    except Exception:
        return 0


def _list_branches(repo_path: Path, default_branch: str) -> list[dict]:
    """本地分支列表：HEAD 信息 + 相对默认分支的领先/落后 + 提交数。"""
    result = subprocess.run(
        [
            "git", "for-each-ref",
            "--format=%(refname:short)|%(objectname)|%(authordate:iso8601)|%(authorname)|%(subject)",
            "refs/heads",
        ],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    branches = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        name, head, date, author, subject = parts
        branches.append({
            "name": name,
            "head": head,
            "head_date": date,
            "head_author": author,
            "subject": subject,
            "is_default": name == default_branch,
        })

    for b in branches:
        b["total_commits"] = _count_commits(repo_path, b["name"])
        if b["is_default"]:
            b["ahead"] = 0
            b["behind"] = 0
            continue
        try:
            lr = subprocess.run(
                ["git", "rev-list", "--left-right", "--count", f"{default_branch}...{b['name']}"],
                cwd=repo_path, capture_output=True, text=True, timeout=15,
            )
            counts = lr.stdout.split()
            left = int(counts[0]) if len(counts) > 0 and counts[0].isdigit() else 0
            right = int(counts[1]) if len(counts) > 1 and counts[1].isdigit() else 0
            b["behind"] = left   # 默认分支有而该分支没有
            b["ahead"] = right   # 该分支独有的提交
        except Exception:
            b["ahead"] = 0
            b["behind"] = 0

    branches.sort(key=lambda x: (not x["is_default"], -x.get("ahead", 0)))
    return branches


def _list_merges(repo_path: Path, count: int = 30) -> list[dict]:
    """最近 N 个 merge commit（含 parents，用于合入关系展示）。"""
    result = subprocess.run(
        ["git", "log", "--all", "--merges", f"-{count}", "--pretty=format:%H|%P|%an|%ai|%s"],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    merges = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        hash_, parents, author, date, message = parts
        merges.append({
            "hash": hash_,
            "short": hash_[:7],
            "parents": [p for p in parents.split() if p],
            "author": author,
            "date": date,
            "message": message,
        })
    return merges


_PR_RE = re.compile(r"\(#(\d+)\)")


def _pr_number(message: str) -> int | None:
    """从 commit message 提取 PR 编号（约定格式：...(#N)）。"""
    m = _PR_RE.search(message or "")
    return int(m.group(1)) if m else None


def _commit_dag(repo_path: Path, limit: int = 200) -> tuple[list[dict], list[dict]]:
    """最近 N 个提交的 DAG（节点 + 父子边，新→旧）。"""
    info = subprocess.run(
        ["git", "rev-list", "--all", f"--max-count={limit}", "--pretty=format:%H|%an|%ai|%s"],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    parents_run = subprocess.run(
        ["git", "rev-list", "--all", "--parents", f"--max-count={limit}"],
        cwd=repo_path, capture_output=True, text=True, timeout=60,
    )
    parent_map = {}
    for line in parents_run.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split()
        if parts:
            parent_map[parts[0]] = parts[1:]

    refs_run = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)|%(objectname)", "refs/heads"],
        cwd=repo_path, capture_output=True, text=True, timeout=15,
    )
    ref_map = {}
    for line in refs_run.stdout.strip().split("\n"):
        if not line:
            continue
        name, head = line.split("|", 1)
        ref_map.setdefault(head, []).append(name)

    nodes = []
    edges = []
    for line in info.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 4:
            continue
        hash_, author, date, message = parts
        parents = parent_map.get(hash_, [])
        nodes.append({
            "id": hash_,
            "short": hash_[:7],
            "author": author,
            "date": date,
            "message": message,
            "is_merge": len(parents) >= 2,
            "parents": parents,
            "refs": ref_map.get(hash_, []),
            "pr_number": _pr_number(message),
        })
        for p in parents:
            edges.append({"source": hash_, "target": p})
    return nodes, edges


def get_git_graph(repo_path: Path, limit: int = 200) -> dict:
    """
    获取分支拓扑 + 合入关系数据（Dashboard「分支拓扑」卡片）。

    Returns:
        dict: {
            "default_branch": str,
            "branches": [{name, head, head_date, head_author, subject, ahead, behind, total_commits, is_default}],
            "merges": [{hash, short, parents, author, date, message}],
            "graph": {"nodes": [{id, short, author, date, message, is_merge, refs}],
                      "edges": [{source, target}]},
        }
    """
    key = str(Path(repo_path).resolve())
    cached = _GIT_GRAPH_CACHE.get(key)
    if cached and time.time() - cached[0] < _GIT_GRAPH_TTL:
        return cached[1]

    default_branch = _default_branch(repo_path)
    result = {
        "default_branch": default_branch,
        "branches": _list_branches(repo_path, default_branch),
        "merges": _list_merges(repo_path),
        "graph": dict(zip(("nodes", "edges"), _commit_dag(repo_path, limit=limit))),
    }
    _GIT_GRAPH_CACHE[key] = (time.time(), result)
    return result


def invalidate_git_graph_cache(repo_path: Path):
    """仓库发生 clone/pull 后清空对应缓存。"""
    try:
        _GIT_GRAPH_CACHE.pop(str(Path(repo_path).resolve()), None)
    except Exception:
        pass
