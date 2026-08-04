"""git 命令层 — 仓库 clone/pull、认证/代理、路径推导与并发锁。"""

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from services import index_service, tracking_service
from services.git_cache import _request_index_background, invalidate_git_graph_cache

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("CODETRACE_CACHE", "/tmp/codetrace"))
_CLONE_THRESHOLD_DEFAULT_KB = 1024 * 1024  # 1GB
_SIZE_CACHE: dict[str, tuple[int | None, float]] = {}
_SIZE_CACHE_TTL = 3600  # 秒

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
        try:
            base_count = int(os.environ.get("GIT_CONFIG_COUNT", "0") or "0")
        except ValueError:
            base_count = 0  # 非数字配置视为无，避免中断
        # URL 作用域配置：只对 github.com 请求带认证头，防跨主机（如 submodule）泄露
        return {
            "GIT_CONFIG_COUNT": str(base_count + 2),
            f"GIT_CONFIG_KEY_{base_count}": "http.https://github.com/.extraHeader",
            f"GIT_CONFIG_VALUE_{base_count}": auth,
            f"GIT_CONFIG_KEY_{base_count + 1}": "http.https://www.github.com/.extraHeader",
            f"GIT_CONFIG_VALUE_{base_count + 1}": auth,
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
    运行 git 网络命令（兼容不同用户的代理/认证环境），按序尝试直到成功：
    1. 配置代理 + 匿名（token 失效时公开仓库/探测不受影响）
    2. 配置代理 + 认证（私有仓库）
    3. 直连 + 匿名
    4. 直连 + 认证
    retry_on_timeout=False 时首轮超时（代理黑洞）直接抛，不继续尝试。
    """
    auth_env = _git_auth_env(repo_url)
    direct_cmd = ["git", "-c", "http.proxy=", "-c", "https.proxy="] + extra_args
    attempts = [(_git_net_args(extra_args), False)]
    if auth_env:
        attempts.append((_git_net_args(extra_args), True))
    attempts.append((direct_cmd, False))
    if auth_env:
        attempts.append((direct_cmd, True))

    last = None
    first_timed_out = False
    for idx, (cmd, use_auth) in enumerate(attempts):
        env = {**os.environ, **auth_env} if use_auth else None
        # 后续轮次递减超时，避免网络完全不通时等待翻倍到 4×timeout
        cur_timeout = timeout if idx == 0 else max(5, timeout // 2)
        try:
            r = subprocess.run(
                cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                timeout=cur_timeout,
            )
            if r.returncode == 0:
                return r
            last = subprocess.CalledProcessError(
                r.returncode, cmd, output=r.stdout, stderr=r.stderr
            )
        except subprocess.TimeoutExpired as e:
            if idx == 0:
                first_timed_out = True
            last = e
        except Exception as e:
            last = e
        if idx == 0 and first_timed_out and not retry_on_timeout:
            # 首轮超时且不要求重试（如 ls-remote）：直接抛，避免代理黑洞翻倍延迟
            raise last
    raise last


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
