"""LangGraph 驱动的 Agent 规划层 — 替代文本协议 ReAct 循环"""

import json
import os
from typing import TypedDict

from langgraph.graph import StateGraph, END
from services.agent.tool_registry import ToolRegistry
from services.llm_service import LLMService


class AgentState(TypedDict):
    """LangGraph 状态"""
    goal: str
    repo_url: str
    findings: list[dict]
    step_count: int
    max_steps: int
    error: str | None
    messages: list[dict]
    final_report: str | None


SYSTEM_PROMPT = """你是一个代码仓库分析 Agent。你的任务是使用提供的工具探索仓库，理解它的结构和健康状况。

策略：
1. 从宏观开始：先看热点文件（repo_health / git_hotspots）
2. 深入重要模块：对热点或可疑文件做更深入的追溯（trace_function / git_log）
3. 关注架构问题：寻找跨文件迁移、高频变更、bug 集中的区域

规则：
- 一次调用一个工具，根据前一步结果决定下一步做什么
- 当收集足够信息后，停止调用工具，输出最终报告
- 通常需要 5-10 步才能形成有意义的结论
- 使用中文输出最终报告

输出格式：
- 有工具调用：正常输出 tool_calls
- 完成分析：输出中文总结（含项目概况、热点、风险、建议）"""


def build_agent(registry: ToolRegistry, llm: LLMService | None = None):
    """构建 LangGraph StateGraph"""

    if llm is None:
        llm = LLMService(
            api_key=os.getenv("LLM_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.getenv("LLM_MODEL", "deepseek-v4-pro"),
        )

    tools_schemas = registry.list_schemas()

    # ── 节点函数 ────────────────────────────────────────────

    def plan_node(state: AgentState) -> dict:
        """调用 LLM function calling，决定下一步"""
        if state["error"]:
            return {"error": state["error"]}

        msg = llm._call_with_tools(state["messages"], tools_schemas)
        tool_calls = msg.get("tool_calls")

        # 构造 assistant 消息加入历史
        assistant_msg = {
            "role": "assistant",
            "content": msg.get("content") or None,
        }
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls

        new_messages = state["messages"] + [assistant_msg]
        updates = {
            "messages": new_messages,
            "step_count": state["step_count"] + 1,
        }

        # LLM 想总结（没调工具）
        if not tool_calls and msg.get("content"):
            updates["final_report"] = msg["content"]

        return updates

    def execute_node(state: AgentState) -> dict:
        """执行 plan_node 产生的工具调用"""
        last_msg = state["messages"][-1]
        tool_calls = last_msg.get("tool_calls", [])
        if not tool_calls:
            return {}

        new_messages = list(state["messages"])
        new_findings = list(state["findings"])

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}

            tool = registry.get(tool_name)
            if not tool:
                result = {"error": f"工具不存在: {tool_name}"}
            else:
                try:
                    result = tool.execute(**args)
                except Exception as e:
                    result = {"error": str(e)}

            record = {"tool": tool_name, "args": args, "result": result}
            new_findings.append(record)

            new_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False, default=str)[:3000],
            })

        return {"messages": new_messages, "findings": new_findings}

    def summarize_node(state: AgentState) -> dict:
        """当 LLM 直接输出总结，或步数耗尽时强制总结"""
        if state.get("final_report"):
            return {"final_report": state["final_report"]}

        # 步数耗尽但 LLM 没给总结 → 用 LLM 把 findings 合成可读报告
        findings_short = []
        for f in state["findings"]:
            name = f["tool"]
            if isinstance(f["result"], dict):
                # 提取关键信息，去掉大段无用字段
                r = {k: v for k, v in f["result"].items() if k not in ("history", "files", "commit_messages")}
                findings_short.append(f"- {name}: {json.dumps(r, ensure_ascii=False)[:300]}")
            elif isinstance(f["result"], list):
                items = [str(x)[:60] for x in f["result"][:4]]
                findings_short.append(f"- {name}: [{', '.join(items)}]")
            else:
                findings_short.append(f"- {name}: {str(f['result'])[:200]}")

        findings_text = "\n".join(findings_short)
        try:
            resp = llm._call([
                {"role": "system", "content": "你是代码仓库分析报告撰写人。根据以下原始分析数据，写一份简洁、有结构的报告。分成：项目概况（重点）、热点与风险（重点）、关键发现（列出）、建议（列出）。不要列举原始工具名和JSON。每条结论用一句话。总字数不超过600字。"},
                {"role": "user", "content": f"目标：{state['goal']}\n\n分析数据：\n{findings_text[:3000]}"},
            ], temperature=0.3)
            report = resp if resp else "（LLM 未能生成报告。）"
        except Exception:
            report = "（报告生成异常。）"

        return {"final_report": report}

    # ── 条件边 ─────────────────────────────────────────────

    def after_plan(state: AgentState) -> str:
        """plan_node 后的路由选择"""
        if state.get("error"):
            return "error"
        if state["step_count"] >= state["max_steps"]:
            return "force_summarize"
        last_msg = state["messages"][-1] if state["messages"] else {}
        if last_msg.get("tool_calls"):
            return "execute"
        return "summarize"

    # ── 构建图 ─────────────────────────────────────────────

    graph = StateGraph(AgentState)

    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("summarize", summarize_node)

    graph.set_entry_point("plan")

    graph.add_conditional_edges(
        "plan",
        after_plan,
        {
            "execute": "execute",
            "summarize": "summarize",
            "force_summarize": "summarize",
            "error": END,
        },
    )

    graph.add_edge("execute", "plan")
    graph.add_edge("summarize", END)

    return graph.compile()


def run_agent(
    registry: ToolRegistry,
    goal: str,
    repo_url: str = "",
    max_steps: int = 15,
) -> dict:
    """
    运行 LangGraph Agent。

    Args:
        registry: 工具注册表。
        goal: 分析目标描述。
        repo_url: 仓库 URL（可选）。
        max_steps: 最大步数。

    Returns:
        dict: {steps, findings, final_report, step_count, error}
    """
    app = build_agent(registry)

    initial: AgentState = {
        "goal": goal,
        "repo_url": repo_url,
        "findings": [],
        "step_count": 0,
        "max_steps": max_steps,
        "error": None,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"目标：{goal}"
                           + (f"\n仓库 URL：{repo_url}" if repo_url else ""),
            },
        ],
        "final_report": None,
    }

    result = app.invoke(initial)

    return {
        "steps": result.get("findings", []),
        "findings": result.get("findings", []),
        "final_report": result.get("final_report"),
        "step_count": result.get("step_count", 0),
        "error": result.get("error"),
    }
