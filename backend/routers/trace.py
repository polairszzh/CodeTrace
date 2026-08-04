import json
import os
import asyncio
import time
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from models.schemas import TraceRequest, TraceResponse, TimelineNode, DiffStats
from services.git_service import clone_or_pull_repo, repo_path_for_url, repo_full_from_url, get_file_commits, get_commit_diff_stats, list_files_at_commit, get_file_content_at_commit, get_file_commit_counts, get_repo_summary, get_git_graph
from services.github_service import GitHubClient
from services.llm_service import LLMService
from services.ast_service import trace_function_across_commits, trace_class_across_commits, extract_symbols_fast
from services.index_service import get_symbols as index_get_symbols, index_fresh, get_index_status
from services import tracking_service
from services.agent import registry
from services.agent.planner import AgentPlanner
from services.agent.graph import run_agent, run_agent_stream
from services.agent.ask_tools import ask_registry
from services.coupling_runner import run_coupling_analysis

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

@router.post("/agent/analyze")
async def agent_analyze(req: TraceRequest, goal: str = ""):
    if not goal:
        goal = f"分析仓库 {req.repo_url}, 找出变更频繁的文件，对关键函数做深度追溯，输出项目活跃度报告"

    planner = AgentPlanner(registry)

    def generate():
        import json
        yield "data: " + json.dumps({"step": 0, "status": "Agent 正在准备，首次分析需要 clone 仓库..."}, ensure_ascii=False) + "\n\n"
        try:
            clone_or_pull_repo(req.repo_url)
        except Exception as e:
            yield "data: " + json.dumps({"step": 0, "error": f"仓库操作失败: {str(e)}"}, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        steps = planner.run(goal + f'\n仓库地址: {req.repo_url}', max_steps=20)
        for step in steps:
            yield f'data: {json.dumps(step, ensure_ascii=False)}\n\n'
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/graph/analyze")
async def graph_analyze(req: TraceRequest, goal: str = ""):
    if not goal:
        goal = f"分析仓库 {req.repo_url}, 找出变更频繁的文件，对关键函数做深度追溯，输出项目活跃度报告"

    try:
        await asyncio.to_thread(clone_or_pull_repo, req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")

    # 并行：主 Agent 报告 + 耦合分析
    report_task = run_agent(registry, goal, repo_url=req.repo_url, max_steps=15)
    coupling_task = run_coupling_analysis(req.repo_url)

    report_raw, coupling_raw = await asyncio.gather(
        report_task, coupling_task, return_exceptions=True
    )

    # 组装 report 部分
    if isinstance(report_raw, Exception):
        report = {
            "goal": goal,
            "repo_url": req.repo_url,
            "step_count": 0,
            "findings_count": 0,
            "findings": [],
            "final_report": None,
            "error": str(report_raw),
        }
    else:
        report = {
            "goal": goal,
            "repo_url": req.repo_url,
            "step_count": report_raw["step_count"],
            "findings_count": len(report_raw.get("findings", [])),
            "findings": report_raw.get("findings", []),
            "final_report": report_raw.get("final_report"),
            "error": report_raw.get("error"),
        }

    # 组装 coupling 部分
    if isinstance(coupling_raw, Exception):
        coupling = {
            "nodes": [],
            "edges": [],
            "total_files": 0,
            "high_risk_count": 0,
            "note": str(coupling_raw),
        }
    else:
        coupling = coupling_raw

    return {"report": report, "coupling": coupling}


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
    if index_fresh(repo_path):
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
            tracking_service.request_tracking(repo_path)
            data["status"] = "refreshing"
    else:
        data["status"] = "ready"
    return data


# ── "问 Agent" 轻量入口 ──────────────────────────────────

ASK_SYSTEM_PROMPT = """你是一个代码仓库分析助手。用户针对某个文件、函数或 commit 向你提问。

你的任务是使用提供的工具获取相关信息，然后回答用户的问题。

可用工具及用途：
- get_commit_diff: 查看某次 commit 改了哪些代码（回答"这次改了啥"时优先用）
- read_file_at_head: 查看文件的当前内容（了解文件整体结构时使用）
- get_file_commits: 查看文件的 commit 历史（了解变更频率和节奏时使用）
- trace_function: 追踪函数的演变历史（回答"这个函数怎么变的"时使用）
- get_pr_info: 查看 PR 的标题和描述（理解变更上下文时使用）

规则：
- 先看用户问了什么，选择最直接的工具获取所需信息
- 如果一个问题需要多个独立的信息（如同时看 diff 和文件内容），可以一次调多个工具
- 最多调 2 次工具就应该能回答
- 用中文回答，简洁直接，不要输出 Markdown 标题以外的格式
- 如果用户问了超出工具能力的问题，如实告知
- 回答时引用你看到的事实，不要说"可能""也许"
"""


class AskRequest(BaseModel):
    repo_url: str
    file_path: str = ""
    function_name: str = ""
    commit_hash: str = ""
    question: str = ""


@router.post("/agent/ask")
async def agent_ask(req: AskRequest):
    """轻量 Agent 入口（SSE 流式）：根据上下文精准回答用户问题，不做全量扫描。"""
    # 构建目标描述
    parts = [f"仓库: {req.repo_url}"]
    if req.file_path:
        parts.append(f"文件: {req.file_path}")
    if req.function_name:
        parts.append(f"函数: {req.function_name}")
    if req.commit_hash:
        parts.append(f"commit: {req.commit_hash}")
    parts.append(f"用户问题: {req.question or '请分析这个上下文中的代码变更情况'}")
    goal = "\n".join(parts)

    try:
        await asyncio.to_thread(clone_or_pull_repo, req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败: {str(e)}")

    async def event_stream():
        async for event in run_agent_stream(
            ask_registry,
            goal=goal,
            repo_url=req.repo_url,
            max_steps=2,
            system_prompt=ASK_SYSTEM_PROMPT,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
