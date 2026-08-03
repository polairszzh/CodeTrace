"""持续追踪服务 — PR 合入后自动对比快照，生成增量洞察报告。

设计（2026-08-03）：
- 触发：clone_or_pull_repo 拉到新提交后后台刷新；/repo/tracking 发现 head 落后也触发
- 快照：结构化指标（totals / top_files / PR 列表），存 INDEX_DIR/tracking/<repo>.json
- 增量：git log 上次HEAD..HEAD 的提交 / PR / churn，结构化对比
- 报告：LLM 翻译增量 → Markdown；LLM 失败回退结构化摘要
- 幂等：head 未变不重复生成；后台线程按仓库去重
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from services.index_service import index_dir

logger = logging.getLogger(__name__)

_PR_RE = re.compile(r"\(#(\d+)\)|(?:^|\s)#(\d+)\b")
_TRACKING_THREADS: dict[str, threading.Thread] = {}
_TRACKING_THREADS_LOCK = threading.Lock()
_STORE_LOCK = threading.Lock()
_MAX_SNAPSHOTS = 10
_REFRESH_ERROR_BACKOFF = 300  # 秒：刷新失败后 5 分钟内不重复触发后台任务


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _store_path(repo_path: Path) -> Path:
    return index_dir() / "tracking" / f"{Path(repo_path).name}.json"


def _git_head(repo_path: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        head = result.stdout.strip()
        return head or None
    except Exception:
        return None


def _load(repo_path: Path) -> dict:
    try:
        path = _store_path(repo_path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning("读取追踪状态失败 %s: %s", repo_path, e)
    return {"repo": Path(repo_path).name, "snapshots": [], "latest_report": None, "updated_at": None}


def _save(repo_path: Path, data: dict) -> bool:
    try:
        with _STORE_LOCK:
            path = _store_path(repo_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        return True
    except Exception as e:
        logger.warning("写入追踪状态失败 %s: %s", repo_path, e)
        return False


def _new_commits(repo_path: Path, from_head: Optional[str]) -> list[dict]:
    """from_head..HEAD 的新提交（新→旧）。from_head 为空返回空列表。"""
    if not from_head:
        return []
    try:
        result = subprocess.run(
            ["git", "log", f"{from_head}..HEAD", "--pretty=format:%H%x00%an%x00%ai%x00%s"],
            cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if result.returncode != 0:
            logger.warning(
                "git log %s..HEAD 失败(%s): %s", from_head, result.returncode,
                result.stderr.strip()[:200],
            )
            return None
        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\x00")
            if len(parts) >= 4:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })
        return commits
    except Exception as e:
        logger.warning("读取新提交失败 %s: %s", repo_path, e)
        return None


def _range_churn(repo_path: Path, from_head: Optional[str], limit: int = 30) -> list[dict]:
    """from_head..HEAD 区间内每文件 churn，按变更行数降序取前 limit。"""
    if not from_head:
        return []
    try:
        result = subprocess.run(
            ["git", "log", f"{from_head}..HEAD", "--numstat", "--pretty=format:__COMMIT__%H"],
            cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if result.returncode != 0:
            logger.warning(
                "git log --numstat %s..HEAD 失败(%s): %s", from_head, result.returncode,
                result.stderr.strip()[:200],
            )
            return None
        churn = {}
        for line in result.stdout.split("\n"):
            line = line.strip()
            if not line or line.startswith("__COMMIT__"):
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, f = parts
            if a == "-" or d == "-":
                continue  # 二进制
            try:
                av, dv = int(a or 0), int(d or 0)
            except ValueError:
                continue
            f = _unquote_git_path(f.strip())  # git 对含特殊字符路径 C 风格转义，统一还原
            if " => " in f:
                f = _unquote_git_path(f.split(" => ", 1)[1].strip())  # 重命名记到新路径
            entry = churn.setdefault(f, {"commits": 0, "additions": 0, "deletions": 0})
            entry["commits"] += 1
            entry["additions"] += av
            entry["deletions"] += dv
        ranked = sorted(
            churn.items(),
            key=lambda kv: (kv[1]["additions"] + kv[1]["deletions"]),
            reverse=True,
        )[:limit]
        return [{"file": f, **stats} for f, stats in ranked]
    except Exception as e:
        logger.warning("读取区间 churn 失败 %s: %s", repo_path, e)
        return None


def _prs_from_commits(commits: list[dict]) -> list[dict]:
    if not commits:
        return []
    prs = []
    seen = set()
    for c in commits:
        pr_number = _extract_pr_number(c["message"])
        if pr_number and pr_number not in seen:
            seen.add(pr_number)
            prs.append({
                "pr_number": pr_number,
                "subject": c["message"],
                "hash": c["hash"][:7],
            })
    return prs


def _unquote_git_path(path: str) -> str:
    """还原 git C 风格转义的路径（\" \\ \t \n \r 等），未加引号时原样返回。"""
    if len(path) < 2 or not path.startswith('"') or not path.endswith('"'):
        return path
    body = path[1:-1]
    out = []
    i = 0
    escapes = {'"': '"', '\\': '\\', 't': '\t', 'n': '\n', 'r': '\r'}
    while i < len(body):
        ch = body[i]
        if ch == '\\' and i + 1 < len(body):
            out.append(escapes.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return ''.join(out)


def _extract_pr_number(message: Optional[str]) -> Optional[int]:
    """提取 PR 编号：兼容 (#123) 与 GitHub「Merge pull request #123」两种形式。"""
    m = _PR_RE.search(message or "")
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def _current_totals(repo_path: Path) -> dict:
    from services.git_service import get_repo_summary  # 延迟导入避免循环依赖

    try:
        summary = get_repo_summary(repo_path)
        return {
            "total_commits": summary.get("total_commits", 0),
            "total_files": summary.get("total_files", 0),
            "total_authors": summary.get("total_authors", 0),
        }
    except Exception as e:
        logger.warning("追踪指标获取失败 %s: %s", repo_path, e)
        return {"total_commits": 0, "total_files": 0, "total_authors": 0}


def _compute_delta(repo_path: Path, old_snapshot: Optional[dict], head: str) -> dict:
    """对比旧快照与当前 HEAD，产出结构化增量。"""
    if old_snapshot is None:
        return {"baseline": True, "head": head, "current_totals": _current_totals(repo_path)}

    commits = _new_commits(repo_path, old_snapshot.get("head"))
    churn = _range_churn(repo_path, old_snapshot.get("head"))
    if commits is None or churn is None:
        return {"error": "增量计算失败（git 区间不可读）", "head": head}
    new_prs = _prs_from_commits(commits)
    old_top = set(old_snapshot.get("top_files") or [])
    new_hot = [c for c in churn if c["file"] not in old_top][:10]
    continued = [c for c in churn if c["file"] in old_top][:10]

    new_totals = _current_totals(repo_path)
    old_totals = old_snapshot
    return {
        "baseline": False,
        "from_head": old_snapshot.get("head"),
        "head": head,
        "new_commits": len(commits),
        "new_prs": new_prs,
        "churn": churn,
        "new_hot_files": new_hot,
        "continued_hot_files": continued,
        "totals_delta": {
            "commits": new_totals.get("total_commits", 0) - old_totals.get("total_commits", 0),
            "files": new_totals.get("total_files", 0) - old_totals.get("total_files", 0),
            "authors": new_totals.get("total_authors", 0) - old_totals.get("total_authors", 0),
        },
        "current_totals": new_totals,
    }


_TRACKING_PROMPT = """你是代码仓库的持续追踪分析助手。以下是仓库自上次快照以来的增量数据（JSON）。请用中文输出一份增量洞察报告（Markdown），结构：
## 变更概览
[本次合入 N 个提交 / M 个 PR 的一句话概述]
## 新增风险热点
[每个新热点文件：它是什么（一句话，从文件名/路径推断）+ 为什么值得关注（churn 数据）]
## 持续热点
[上次就在、这次继续活跃的文件及其变化]
## 健康度变化
[提交数/文件数/贡献者增减]
要求：只基于给定数据，不要编造文件名或数据；简洁，正文 400 字以内。"""


def _build_fallback_report(delta: dict) -> str:
    """LLM 不可用时的结构化摘要。"""
    if delta.get("baseline"):
        return "## 基线已建立\n首次快照已生成，后续 PR 合入后将自动产出增量洞察报告。"
    lines = ["## 变更概览",
             f"- 新提交 {delta.get('new_commits', 0)} 个"
             + (f"，含 {len(delta.get('new_prs', []))} 个 PR" if delta.get('new_prs') else "")]
    lines.append("")
    lines.append("## 新增风险热点")
    if delta.get("new_hot_files"):
        for c in delta["new_hot_files"][:5]:
            lines.append(f"- {c['file']}：+{c['additions']} / -{c['deletions']}（{c['commits']} 次提交）")
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 持续热点")
    if delta.get("continued_hot_files"):
        for c in delta["continued_hot_files"][:5]:
            lines.append(f"- {c['file']}：+{c['additions']} / -{c['deletions']}")
    else:
        lines.append("- 无")
    td = delta.get("totals_delta", {})
    lines.append("")
    lines.append("## 健康度变化")
    lines.append(f"- 提交数 {td.get('commits', 0):+d} · 文件数 {td.get('files', 0):+d} · 贡献者 {td.get('authors', 0):+d}")
    return "\n".join(lines)


def _generate_report(delta: dict, llm=None) -> dict:
    """生成增量报告：LLM 优先，失败回退结构化摘要。"""
    if delta.get("baseline"):
        md = _build_fallback_report(delta)
        return {"markdown": md, "structured": delta, "generated_by": "baseline", "head": delta.get("head")}
    try:
        if llm is None:
            from services.llm_service import LLMService
            llm = LLMService()
        content = json.dumps(delta, ensure_ascii=False, default=str)
        md = llm._call(
            [
                {"role": "system", "content": _TRACKING_PROMPT},
                {"role": "user", "content": content[:4000]},
            ],
            temperature=0.3,
        )
        if not md or len(md.strip()) < 20:
            raise ValueError("LLM 返回空报告")
        return {"markdown": md, "structured": delta, "generated_by": "llm", "head": delta.get("head")}
    except Exception as e:
        logger.warning("追踪报告 LLM 生成失败，回退结构化摘要: %s", e)
        return {
            "markdown": _build_fallback_report(delta),
            "structured": delta,
            "generated_by": "fallback",
            "head": delta.get("head"),
        }


def refresh_tracking(repo_path, llm=None) -> dict:
    """刷新追踪状态：head 未变则幂等跳过；否则计算增量、生成报告、追加快照。"""
    try:
        repo_path = Path(repo_path)
        data = _load(repo_path)
        head = _git_head(repo_path)
        if not head:
            logger.warning("追踪刷新失败：无法获取 HEAD %s", repo_path)
            return data

        snapshots = data.get("snapshots", [])
        if snapshots and snapshots[-1].get("head") == head:
            return data  # 幂等：无新提交

        old = snapshots[-1] if snapshots else None
        delta = _compute_delta(repo_path, old, head)
        if delta.get("error"):
            logger.warning("追踪增量计算失败 %s: %s", repo_path, delta["error"])
            data["refresh_error"] = {"at": _now(), "message": str(delta.get("error"))}
            _save(repo_path, data)
            return data  # 区间不可读时跳过本次刷新，不生成误导报告
        report = _generate_report(delta, llm)
        data.pop("refresh_error", None)

        from services.git_service import get_top_changed_files  # 延迟导入避免循环依赖
        try:
            top_files = get_top_changed_files(repo_path, 10)
        except Exception as e:
            logger.warning("快照 top_files 获取失败 %s: %s", repo_path, e)
            top_files = []

        snapshot = {
            "head": head,
            "created_at": _now(),
            "top_files": top_files,
            "prs": delta.get("new_prs", []),
            **delta.get("current_totals", _current_totals(repo_path)),
        }
        snapshots.append(snapshot)
        data["snapshots"] = snapshots[-_MAX_SNAPSHOTS:]
        data["latest_report"] = report
        data["updated_at"] = _now()
        _save(repo_path, data)
        return data
    except Exception as e:
        logger.warning("追踪刷新异常 %s: %s", repo_path, e)
        return _load(repo_path)


def get_tracking(repo_path) -> dict:
    """读取追踪状态（不触发生成），附 head 与 stale 标记。"""
    data = _load(repo_path)
    head = _git_head(repo_path)
    data["head"] = head
    latest = data.get("latest_report")
    data["stale"] = bool(head) and (latest is None or latest.get("head") != head)
    return data


def _background(repo_path: Path):
    name = Path(repo_path).name
    try:
        refresh_tracking(repo_path)
    except Exception as e:
        logger.warning("后台追踪失败 %s: %s", repo_path, e)
    finally:
        with _TRACKING_THREADS_LOCK:
            if _TRACKING_THREADS.get(name) is threading.current_thread():
                del _TRACKING_THREADS[name]


def request_tracking(repo_path) -> bool:
    """后台异步刷新追踪（幂等：同仓库已有线程则跳过）。"""
    try:
        repo_path = Path(repo_path)
        name = repo_path.name
        with _TRACKING_THREADS_LOCK:
            err = _load(repo_path).get("refresh_error")
            if err and _within_backoff(err.get("at")):
                return False
            existing = _TRACKING_THREADS.get(name)
            if existing and existing.is_alive():
                return False
            t = threading.Thread(
                target=_background, args=(repo_path,),
                name=f"track-{name}", daemon=True,
            )
            _TRACKING_THREADS[name] = t
            t.start()
            return True
    except Exception:
        return False


def _within_backoff(at: Optional[str]) -> bool:
    if not at:
        return False
    try:
        ts = datetime.fromisoformat(at).timestamp()
        return time.time() - ts < _REFRESH_ERROR_BACKOFF
    except Exception:
        return False


def in_backoff(repo_path) -> bool:
    """公开：刷新失败是否仍处于退避窗口（端点据此决定是否重试）。"""
    try:
        err = _load(repo_path).get("refresh_error")
        return bool(err and _within_backoff(err.get("at")))
    except Exception:
        return False
