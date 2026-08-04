"""repo 域路由 — 仓库文件树、符号、风险、仪表盘、Git Graph、索引与追踪。"""

import asyncio
import json
import logging
import os
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from services import tracking_service
from services.ast_service import extract_symbols_fast
from services.git_runner import (
    clone_or_pull_repo,
    repo_full_from_url,
    repo_path_for_url,
)
from services.git_stats import (
    get_file_commit_counts,
    get_file_content_at_commit,
    get_git_graph,
    get_repo_summary,
    list_files_at_commit,
)
from services.github_service import GitHubClient
from services.index_service import get_index_status, index_fresh
from services.index_service import get_symbols as index_get_symbols

router = APIRouter()

logger = logging.getLogger(__name__)

github = GitHubClient(token=os.getenv("GITHUB_TOKEN", ""))


@router.get("/repo/files")
async def repo_files(repo_url: str, path: str = ""):
    """返回仓库目录结构。无 path 时返回顶层，有 path 时返回该路径下的子节点。"""
    try:
        repo_path = await asyncio.to_thread(clone_or_pull_repo, repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")

    try:
        files = await asyncio.to_thread(list_files_at_commit, repo_path, "HEAD")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件列表获取失败：{str(e)}")

    # 过滤路径前缀
    prefix = path.strip("/")
    if prefix:
        files = [f for f in files if f.startswith(prefix + "/") or f == prefix]

    # 构建树
    seen = set()
    entries = []
    for f in files:
        if prefix:
            rel = f[len(prefix) + 1:] if f.startswith(prefix + "/") else f
        else:
            rel = f
        parts = rel.split("/")
        top = parts[0]
        if top not in seen:
            seen.add(top)
            entries.append({
                "name": top,
                "path": (prefix + "/" + top) if prefix else top,
                "type": "dir" if len(parts) > 1 else "file",
            })

    entries.sort(key=lambda x: (x["type"] != "dir", x["name"]))
    return {"entries": entries}


@router.get("/repo/symbols")
async def repo_symbols(repo_url: str, file_path: str):
    """返回指定文件当前 HEAD 的所有函数名和 class 名（使用轻量提取器）。"""
    try:
        repo_path = await asyncio.to_thread(clone_or_pull_repo, repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")

    # 索引优先：HEAD 符号直接走 SQLite，异常回退 git 读取
    if await asyncio.to_thread(index_fresh, repo_path):
        try:
            symbols = index_get_symbols(repo_path, file_path)
            if symbols is not None:
                return symbols
        except Exception:
            pass

    try:
        content = await asyncio.to_thread(get_file_content_at_commit, repo_path, "HEAD", file_path)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"文件获取失败：{str(e)}")

    return await asyncio.to_thread(extract_symbols_fast, content, file_path)


@router.get("/repo/file-risks")
async def repo_file_risks(repo_url: str):
    """轻量：返回每个文件的风险等级，用于文件树着色。"""
    try:
        repo_path = await asyncio.to_thread(clone_or_pull_repo, repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")

    try:
        counts = await asyncio.to_thread(get_file_commit_counts, repo_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"风险分析失败：{str(e)}")

    # Percentile-based risk assignment
    if not counts:
        return {"risks": {}}

    values = sorted(counts.values())
    n = len(values)
    p80 = values[int(n * 0.8)] if n > 5 else 0
    p50 = values[int(n * 0.5)] if n > 2 else 0

    risks = {}
    for filepath, count in counts.items():
        if count >= p80 and p80 > 0:
            risks[filepath] = "high"
        elif count >= p50 and p50 > 0:
            risks[filepath] = "medium"
        else:
            risks[filepath] = "low"

    return {"risks": risks}


@router.get("/repo/dashboard")
async def repo_dashboard(repo_url: str):
    """仓库仪表盘数据：概要统计 + 风险分布 + 近期活动。"""
    try:
        repo_path = await asyncio.to_thread(clone_or_pull_repo, repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")

    summary = await asyncio.to_thread(get_repo_summary, repo_path)

    # Risk distribution
    try:
        counts = await asyncio.to_thread(get_file_commit_counts, repo_path)
        values = sorted(counts.values())
        n = len(values)
        p80 = values[int(n * 0.8)] if n > 5 else 0
        p50 = values[int(n * 0.5)] if n > 2 else 0
        high = sum(1 for c in counts.values() if c >= p80 and p80 > 0)
        medium = sum(1 for c in counts.values() if p50 <= c < p80)
        low = sum(1 for c in counts.values() if c < p50)
        risk_dist = {"high": high, "medium": medium, "low": low}
    except Exception:
        risk_dist = {"high": 0, "medium": 0, "low": 0}

    return {
        "summary": summary,
        "risk_distribution": risk_dist,
    }


@router.get("/repo/git-graph")
async def repo_git_graph(repo_url: str):
    """Git Graph：分支拓扑 + 合入关系（Dashboard 补充）。"""
    if not repo_full_from_url(repo_url):
        raise HTTPException(status_code=400, detail="仓库地址格式错误")
    try:
        repo_path = await asyncio.to_thread(clone_or_pull_repo, repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")
    try:
        return await asyncio.to_thread(get_git_graph, repo_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Git Graph 数据获取失败：{str(e)}")


@router.get("/repo/pr-info")
async def repo_pr_info(repo_url: str, pr_number: int):
    """PR 详情（标题/正文/状态），供 Git Graph 展开面板应用内查看。"""
    repo_full = repo_full_from_url(repo_url)
    if not repo_full:
        raise HTTPException(status_code=400, detail="仓库地址格式错误")
    try:
        info = await asyncio.to_thread(github.get_pr_info, repo_full, pr_number)
    except Exception as e:
        logger.warning("PR 查询异常 repo=%s pr=%s: %s", repo_full, pr_number, e)
        raise HTTPException(status_code=502, detail=f"PR #{pr_number} 查询失败：{e}")
    if info is None:
        raise HTTPException(status_code=404, detail=f"PR #{pr_number} 信息获取失败")
    return info


@router.get("/repo/index-status")
async def repo_index_status(repo_url: str):
    """SSE：仓库索引构建进度（排队/扫描/文件/符号/完成/失败）。"""

    async def event_stream():
        repo_path = repo_path_for_url(repo_url)
        if not repo_path.exists():
            # 仓库尚未克隆，无索引任务可等
            yield f"data: {json.dumps({'repo': repo_url, 'fresh': False, 'status': 'not_found', 'message': '仓库尚未克隆'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        deadline = time.time() + 300
        not_started_deadline = time.time() + 15
        while True:
            # 状态文件读取是本地小文件（微秒级），直接读；
            # 新鲜度检查可能跑 git rev-parse 子进程，放线程池避免阻塞事件循环
            status = get_index_status(repo_path)
            fresh = await asyncio.to_thread(index_fresh, repo_path)
            if status is None:
                # 索引尚未启动：给后台线程写初始状态留出窗口，超时仍无则视为未开始结束
                while (
                    status is None
                    and not fresh
                    and time.time() < not_started_deadline
                ):
                    await asyncio.sleep(2)
                    status = get_index_status(repo_path)
                    fresh = await asyncio.to_thread(index_fresh, repo_path)
                if status is None and not fresh:
                    yield f"data: {json.dumps({'repo': repo_url, 'fresh': False, 'status': 'not_started', 'message': '索引尚未启动'}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
            payload = {"repo": repo_url, "fresh": fresh}
            if status:
                payload.update(status)
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if (
                fresh
                or (status and status.get("status") in ("done", "error"))
                or time.time() > deadline
            ):
                break
            await asyncio.sleep(2)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/repo/tracking")
async def repo_tracking(repo_url: str):
    """持续追踪：最新增量报告 + 快照状态；head 落后时后台触发刷新。"""
    if not repo_full_from_url(repo_url):
        raise HTTPException(status_code=400, detail="仓库地址格式错误")
    try:
        # clone_or_pull_repo 自带 60s 内存 TTL：轮询期间大部分命中缓存，
        # 每约 60s 才做一次远程检查，兼顾远程感知与网络开销
        repo_path = await asyncio.to_thread(clone_or_pull_repo, repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")
    data = await asyncio.to_thread(tracking_service.get_tracking, repo_path)
    if not data.get("head"):
        data["status"] = "error"
        data["message"] = "仓库没有提交，无法建立追踪基线"
        return data
    if data.get("stale"):
        if data.get("refresh_error") and tracking_service.in_backoff(repo_path):
            data["status"] = "error"
            data["message"] = "增量计算暂不可用（稍后自动重试）：" + str(data["refresh_error"].get("message", "未知原因"))
            data["retry_after"] = tracking_service.refresh_error_remaining(repo_path)
        else:
            if not tracking_service.request_tracking(repo_path) \
                    and not tracking_service.tracking_thread_alive(repo_path):
                data["status"] = "error"
                data["message"] = "追踪后台任务启动失败，请稍后重试"
            else:
                data["status"] = "refreshing"
    else:
        data["status"] = "ready"
    return data
