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

输出格式 — 完成分析时，按以下结构化 Markdown 模板输出最终报告（不要用代码块包裹，直接输出 Markdown）：

## 项目总览
[2-3 段：项目定位（从代码推断这是做什么的）、架构分层（主要模块及其职责）、当前阶段（快速迭代/稳定维护/重构期）、活跃度]

## 变更热点
按模块/文件列出，每个项目包含：
- 这个模块负责什么（一句话说清，让不熟悉项目的人也能理解）
- 变更频率和原因
- **意味着什么**：这个变更模式对项目的影响

## 风险分析
每项风险包含：
- 风险描述
- 为什么是风险，不处理会怎样
- 建议方向

## 改进建议
每条建议区分视角：
- **对开发者**：具体可行的代码/架构改进
- **对管理者**：资源/流程/优先级层面的建议

要求：
- 每个模块/文件名首次出现时，紧跟一句话说明它是什么
- 每条结论同时包含"事实"和"解读"两层
- 总字数 800-1200 字
- ⚠️ 严禁输出"已收集足够信息""现在可以输出报告"等占位文本，直接开始写报告正文"""


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
            content = msg["content"].strip()
            # 检测占位文本：太短或像"已收集够信息"之类的空话 → 拒绝，重试一次
            placeholder_patterns = ["已收集", "足够的信息", "可以输出", "now i can", "i've gathered", "i have collected"]
            is_placeholder = len(content) < 80 or any(p in content.lower() for p in placeholder_patterns)
            if is_placeholder:
                # 追加拒绝消息，重新调一次 LLM
                retry_messages = new_messages + [{
                    "role": "user",
                    "content": "不要输出占位文本，直接按要求的 Markdown 格式输出完整的分析报告。开始写正文。"
                }]
                retry = llm._call_with_tools(retry_messages, tools_schemas)
                retry_tool_calls = retry.get("tool_calls")
                retry_content = retry.get("content", "").strip()
                # 重试后如果还是占位或无内容，只能 fallthrough 到 summarize
                if not retry_tool_calls and (not retry_content or len(retry_content) < 80):
                    updates["messages"] = new_messages  # 不追加重试记录，让 summarize 兜底
                elif retry_tool_calls:
                    retry_asst = {"role": "assistant", "content": retry_content or None, "tool_calls": retry_tool_calls}
                    updates["messages"] = retry_messages + [retry_asst]
                    updates["step_count"] = state["step_count"] + 1
                else:
                    updates["final_report"] = retry_content
            else:
                updates["final_report"] = content

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
                {"role": "system", "content": "你是代码仓库分析报告撰写人。根据以下原始分析数据，按结构化 Markdown 写一份报告。模板：\n\n"
                "## 项目总览\n[项目定位、架构分层、当前阶段、活跃度]\n\n"
                "## 变更热点\n每项：模块名（一句话说明它是做什么的）+ 变更情况 + **意味着什么**\n\n"
                "## 风险分析\n每项：风险描述 + 为什么不处理会怎样 + 建议\n\n"
                "## 改进建议\n每项：对开发者的建议 + 对管理者的建议\n\n"
                "要求：每个模块首次出现时紧跟一句话解释、每条结论有事实+解读两层、总字数 800-1200 字。不要列举原始工具名和 JSON。"},
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
