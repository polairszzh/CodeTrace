import os
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from models.schemas import TraceRequest, TraceResponse, TimelineNode, DiffStats
from services.git_service import clone_or_pull_repo, get_file_commits, get_commit_diff_stats
from services.github_service import GitHubClient
from services.llm_service import LLMService
from services.ast_service import trace_function_across_commits, trace_class_across_commits
from services.agent import registry
from services.agent.planner import AgentPlanner
from services.agent.graph import run_agent
from services.coupling_runner import run_coupling_analysis

router = APIRouter()

github = GitHubClient(token=os.getenv("GITHUB_TOKEN", ""))
llm = LLMService(
    api_key=os.getenv("LLM_API_KEY", ""),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    model=os.getenv("LLM_MODEL", "deepseek-v4-pro")
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
    try:
        parts = req.repo_url.rstrip("/").split("/")
        repo_owner, repo_name = parts[-2], parts[-1].replace(".git", "")
        repo_full = f"{repo_owner}/{repo_name}" 
    except Exception:
        raise HTTPException(status_code=400, detail="仓库格式地址错误")
    
    # 2. clone/pull 仓库
    try:
        repo_path = clone_or_pull_repo(req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败: {str(e)}")
    
    # 3. 获取文件 commit 历史
    commits = get_file_commits(repo_path, req.file_path)
    if not commits:
        raise HTTPException(status_code=404, detail="该文件无变更历史")
    
    # 4. 构建 timeline
    timeline = []
    for c in commits:
        pr_number = github.extract_pr_number(c["message"])
        pr_info = None
        if pr_number:
            pr_info = github.get_pr_info(repo_full, pr_number)
        
        diff_stats = get_commit_diff_stats(repo_path, c["hash"])
        llm_result = llm.classify_and_summarize(
            commit_message=c["message"],
            pr_title=pr_info.get("title") if pr_info else None,
            pr_description=pr_info.get("body") if pr_info else None
        )

        node = TimelineNode(
            commit_hash=c["hash"],
            author=c["author"],
            date=c["date"],
            message=c["message"],
            pr_number=pr_number,
            pr_title=pr_info.get("title") if pr_info else None,
            change_type=llm_result.get("change_type", "chore"),
            summary=llm_result.get("summary", c["message"][:50]),
            diff_stats=DiffStats(**diff_stats)
        )
        timeline.append(node)

    return TraceResponse(
        repo=repo_full,
        file_path=req.file_path,
        timeline=timeline,
        commit_count=len(timeline),
    )

@router.post("/trace/function")
async def trace_function(req: TraceRequest, function_name: str = ""):
    if not function_name:
        raise HTTPException(status_code=400, detail="请提供函数名")
    
    try:
        parts = req.repo_url.rstrip("/").split("/")
        repo_owner, repo_name = parts[-2], parts[-1].replace(".git", "")
        repo_full = f"{repo_owner}/{repo_name}"
    except Exception:
        raise HTTPException(status_code=400, detail="仓库地址格式有误")
    
    try:
        repo_path = clone_or_pull_repo(req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")
    
    result = trace_function_across_commits(repo_path, req.file_path, function_name)
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

    try:
        parts = req.repo_url.rstrip("/").split("/")
        repo_owner, repo_name = parts[-2], parts[-1].replace(".git", "")
        repo_full = f"{repo_owner}/{repo_name}"
    except Exception:
        raise HTTPException(status_code=400, detail="仓库地址格式有误")

    try:
        repo_path = clone_or_pull_repo(req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")

    result = trace_class_across_commits(repo_path, req.file_path, class_name)
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
        clone_or_pull_repo(req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")

    # 并行：主 Agent 报告 + 耦合分析
    report_task = asyncio.to_thread(
        run_agent, registry, goal, repo_url=req.repo_url, max_steps=15
    )
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