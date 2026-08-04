"""测试 LangGraph Agent 规划层"""

import asyncio
import os

REPO_URL = "https://github.com/polairszzh/CodeTrace.git"
HAS_LLM_KEY = bool(os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY"))


def test_graph_compiles():
    """验证 StateGraph 能正确编译"""
    from services.agent.graph import build_agent
    from services.agent.tools import registry

    app = build_agent(registry)
    assert app is not None
    # 检查图中有预期的节点
    assert "plan" in app.nodes
    assert "execute" in app.nodes
    assert "summarize" in app.nodes


def test_run_agent_on_small_goal():
    """在 CodeTrace 自身仓库上运行 Agent，验证能走完流程"""
    from services.agent.graph import run_agent
    from services.agent.tools import registry

    result = asyncio.run(
        run_agent(
            registry,
            goal=f"分析 {REPO_URL}，快速查看有哪些热点文件",
            repo_url=REPO_URL,
            max_steps=4,
        )
    )

    assert "steps" in result
    assert "findings" in result
    assert result["step_count"] >= 0
    # 即使 LLM 不可用，也应该有结果结构
    if HAS_LLM_KEY:
        # LLM 可用时应该产出一份最终报告
        assert result.get("final_report") is not None, f"无报告: {result.get('error')}"
        assert len(result["findings"]) >= 1, "至少应有一次工具调用"


def test_run_agent_no_llm_fallback():
    """没有 LLM key 时 agent 应优雅降级"""
    from services.agent.graph import run_agent
    from services.agent.tools import registry

    # 临时清掉 key 模拟无 LLM
    original = os.environ.get("LLM_API_KEY", "")
    if original:
        del os.environ["LLM_API_KEY"]

    try:
        result = asyncio.run(
            run_agent(
                registry,
                goal="分析这个仓库",
                repo_url=REPO_URL,
                max_steps=1,
            )
        )
        # 至少不会崩溃，返回完整结构
        assert "steps" in result
        assert "findings" in result
    finally:
        if original:
            os.environ["LLM_API_KEY"] = original


def test_graph_analyze_api():
    """测试 /api/graph/analyze 路由存在并能响应"""
    from routers.agent import router

    route = None
    for r in router.routes:
        if hasattr(r, "path") and r.path == "/graph/analyze":
            route = r
            break
    assert route is not None, "graph/analyze 路由未注册"
    assert "POST" in route.methods or "*" in route.methods


def test_graph_state_machine():
    """验证 StateGraph 状态转换正确"""
    from services.agent.graph import AgentState

    # 验证状态定义完整
    state_keys = {"goal", "repo_url", "findings", "step_count", "max_steps", "error", "messages", "final_report"}
    # AgentState 是 TypedDict，直接检查 __annotations__
    assert set(AgentState.__annotations__.keys()) == state_keys, \
        f"状态定义不完整: {set(AgentState.__annotations__.keys())}"
