"""测试架构级风险检测 — 耦合趋势"""

REPO_URL = "https://github.com/polairszzh/CodeTrace.git"


def test_get_co_change_trends_structure():
    """验证 co-change 趋势数据返回正确结构"""
    from services.git_service import get_co_change_trends, clone_or_pull_repo

    repo = clone_or_pull_repo(REPO_URL)
    trends = get_co_change_trends(repo, window_days=30)

    assert isinstance(trends, list)
    if trends:
        t = trends[0]
        assert "file" in t
        assert "recent_partners" in t
        assert "old_partners" in t
        assert "coupling_growth" in t
        assert "boundary_crossings" in t
        assert "risk" in t
        assert isinstance(t["coupling_growth"], float)


def test_get_co_change_trends_ordering():
    """验证结果按风险排序"""
    from services.git_service import get_co_change_trends, clone_or_pull_repo

    repo = clone_or_pull_repo(REPO_URL)
    trends = get_co_change_trends(repo, window_days=30)

    if len(trends) >= 3:
        # 高风险的应该排在前面
        risks = [t["risk"] for t in trends]
        # 至少第一个不是 low（如果数据充足的话）
        assert risks[0] in ("high", "medium")


def test_get_co_change_trends_window_param():
    """验证可以指定不同的时间窗口"""
    from services.git_service import get_co_change_trends, clone_or_pull_repo

    repo = clone_or_pull_repo(REPO_URL)
    trends_60 = get_co_change_trends(repo, window_days=60)
    assert isinstance(trends_60, list)


def test_coupling_risk_tool_registered():
    """验证 coupling_risk 工具已注册"""
    from services.agent.tools import registry

    tool = registry.get("coupling_risk")
    assert tool is not None
    assert "耦合" in tool.description or "coupling" in tool.description


def test_analyze_coupling_trends_llm():
    """验证 LLM 耦合分析不崩溃"""
    from services.llm_service import LLMService

    llm = LLMService()
    sample = [
        {"file": "backend/services/auth.py", "recent_partners": 8, "old_partners": 3,
         "coupling_growth": 1.67, "boundary_crossings": 5, "risk": "high"},
        {"file": "backend/routers/api.py", "recent_partners": 4, "old_partners": 5,
         "coupling_growth": -0.2, "boundary_crossings": 2, "risk": "low"},
    ]
    result = llm.analyze_coupling_trends(sample)
    assert len(result) == 2
    for r in result:
        assert "warning" in r
        assert "suggestion" in r
