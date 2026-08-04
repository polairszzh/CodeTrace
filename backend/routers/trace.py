"""trace 域路由 — 文件/函数/类变更时间线追踪。"""

import asyncio
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException

from models.schemas import DiffStats, TimelineNode, TraceRequest, TraceResponse
from services.ast_service import (
    trace_class_across_commits,
    trace_function_across_commits,
)
from services.git_runner import clone_or_pull_repo, repo_full_from_url
from services.git_stats import (
    get_commit_diff_stats,
    get_file_commits,
)
from services.github_service import GitHubClient
from services.llm_service import LLMService

router = APIRouter()

logger = logging.getLogger(__name__)

github = GitHubClient(token=os.getenv("GITHUB_TOKEN", ""))
llm = LLMService(
    api_key=os.getenv("LLM_API_KEY", ""),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    model=os.getenv("LLM_MODEL", "deepseek-v4-pro")
)


def _build_file_timeline(
    repo_path, repo_full: str, file_path: str
) -> Optional["TraceResponse"]:
    """同步构建文件时间线（在 to_thread 中执行，避免阻塞事件循环）。"""
    commits = get_file_commits(repo_path, file_path)
    if not commits:
        return None  # 无历史由路由层抛 404，避免依赖线程异常传播

    timeline = []
    warnings = []
    for c in commits:
        pr_number = github.extract_pr_number(c["message"])
        pr_info = None
        if pr_number:
            try:
                pr_info = github.get_pr_info(repo_full, pr_number)
            except Exception as e:
                logger.warning("PR 信息获取失败 repo=%s pr=%s: %s", repo_full, pr_number, e)
                warnings.append(f"PR #{pr_number} 信息获取失败，已跳过")

        try:
            diff_stats = get_commit_diff_stats(repo_path, c["hash"])
        except Exception as e:
            logger.warning("diff 统计失败 hash=%s: %s", c["hash"], e)
            diff_stats = {"additions": 0, "deletions": 0, "files_changed": 0}
            warnings.append(f"提交 {c['hash'][:7]} diff 统计失败，按 0 处理")

        try:
            llm_result = llm.classify_and_summarize(
                commit_message=c["message"],
                pr_title=pr_info.get("title") if pr_info else None,
                pr_description=pr_info.get("body") if pr_info else None,
            )
        except Exception as e:
            logger.warning("LLM 分类失败 hash=%s: %s", c["hash"], e)
            llm_result = {}
            warnings.append(f"提交 {c['hash'][:7]} LLM 分类失败，使用默认摘要")

        node = TimelineNode(
            commit_hash=c["hash"],
            author=c["author"],
            date=c["date"],
            message=c["message"],
            pr_number=pr_number,
            pr_title=pr_info.get("title") if pr_info else None,
            change_type=llm_result.get("change_type", "chore"),
            summary=llm_result.get("summary", c["message"][:50]),
            diff_stats=DiffStats(**diff_stats),
        )
        timeline.append(node)

    return TraceResponse(
        repo=repo_full,
        file_path=file_path,
        timeline=timeline,
        commit_count=len(timeline),
        warnings=warnings,
    )


@router.post("/trace", response_model=TraceResponse)
async def trace_file(req: TraceRequest):
    """
    跟踪指定仓库中某个文件的修改历史，并生成时间线。

    Args:
        req (TraceRequest): 包含仓库 URL 和文件路径的请求体。

    Returns:
        TraceResponse: 包含时间线和提交统计信息的响应体。
    """
    # 1. 解析 repo_url
    repo_full = repo_full_from_url(req.repo_url)
    if not repo_full:
        raise HTTPException(status_code=400, detail="仓库格式地址错误")

    # 2. clone/pull 仓库
    try:
        repo_path = await asyncio.to_thread(clone_or_pull_repo, req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败: {str(e)}")

    # 3-4. 构建 timeline（同步 IO + LLM，放线程池执行）
    result = await asyncio.to_thread(_build_file_timeline, repo_path, repo_full, req.file_path)
    if result is None:
        raise HTTPException(status_code=404, detail="该文件无变更历史")
    return result


@router.post("/trace/function")
async def trace_function(req: TraceRequest, function_name: str = ""):
    if not function_name:
        raise HTTPException(status_code=400, detail="请提供函数名")

    repo_full = repo_full_from_url(req.repo_url)
    if not repo_full:
        raise HTTPException(status_code=400, detail="仓库地址格式有误")

    try:
        repo_path = await asyncio.to_thread(clone_or_pull_repo, req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")

    result = await asyncio.to_thread(
        trace_function_across_commits, repo_path, req.file_path, function_name
    )
    history = result["history"]
    migration_path = result.get("migration_path", [])

    note = None
    if not history:
        note = "未在文件历史中找到该函数，请检查函数名是否正确"
    elif migration_path:
        note = f"该函数发生了 {len(migration_path)} 次跨文件迁移"

    return {
        "repo": repo_full,
        "file_path": req.file_path,
        "function_name": function_name,
        "history": history,
        "migration_path": migration_path,
        "commit_count": len(history),
        "note": note,
    }


@router.post("/trace/class")
async def trace_class(req: TraceRequest, class_name: str = ""):
    if not class_name:
        raise HTTPException(status_code=400, detail="请提供 class 名")

    repo_full = repo_full_from_url(req.repo_url)
    if not repo_full:
        raise HTTPException(status_code=400, detail="仓库地址格式有误")

    try:
        repo_path = await asyncio.to_thread(clone_or_pull_repo, req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")

    result = await asyncio.to_thread(
        trace_class_across_commits, repo_path, req.file_path, class_name
    )
    history = result["history"]
    migration_path = result.get("migration_path", [])

    note = None
    if not history:
        note = "未在文件历史中找到该 class，请检查 class 名是否正确"
    elif migration_path:
        note = f"该 class 发生了 {len(migration_path)} 次跨文件迁移"

    return {
        "repo": repo_full,
        "file_path": req.file_path,
        "class_name": class_name,
        "history": history,
        "migration_path": migration_path,
        "commit_count": len(history),
        "note": note,
    }
