"""agent 域路由 — 全量 Agent 分析、Graph 分析、"问 Agent" 轻量入口。"""

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from models.schemas import TraceRequest
from services.agent import registry
from services.agent.ask_tools import ask_registry
from services.agent.graph import run_agent, run_agent_stream
from services.agent.planner import AgentPlanner
from services.coupling_runner import run_coupling_analysis
from services.git_runner import clone_or_pull_repo

router = APIRouter()


@router.post("/agent/analyze")
async def agent_analyze(req: TraceRequest, goal: str = ""):
    if not goal:
        goal = f"分析仓库 {req.repo_url}, 找出变更频繁的文件，对关键函数做深度追溯，输出项目活跃度报告"

    planner = AgentPlanner(registry)

    def generate():
        yield "data: " + json.dumps({"step": 0, "status": "Agent 正在准备，首次分析需要 clone 仓库..."}, ensure_ascii=False) + "\n\n"
        try:
            clone_or_pull_repo(req.repo_url)
        except Exception as e:
            yield "data: " + json.dumps({"step": 0, "error": f"仓库操作失败: {str(e)}"}, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        try:
            steps = planner.run(goal + f'\n仓库地址: {req.repo_url}', max_steps=20)
            for step in steps:
                yield f'data: {json.dumps(step, ensure_ascii=False)}\n\n'
        except Exception as e:
            yield "data: " + json.dumps({"step": 0, "error": f"Agent 执行失败: {str(e)}"}, ensure_ascii=False) + "\n\n"
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
        try:
            async for event in run_agent_stream(
                ask_registry,
                goal=goal,
                repo_url=req.repo_url,
                max_steps=2,
                system_prompt=ASK_SYSTEM_PROMPT,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'Agent 执行失败: {str(e)}'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
