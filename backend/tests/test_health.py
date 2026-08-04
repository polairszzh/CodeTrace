"""测试升级版健康度检测"""

REPO_URL = "https://github.com/polairszzh/CodeTrace.git"


def test_get_file_health_stats_structure():
    """验证 get_file_health_stats 返回正确结构"""
    from services.git_runner import clone_or_pull_repo
    from services.git_stats import get_file_health_stats

    repo = clone_or_pull_repo(REPO_URL)
    stats = get_file_health_stats(repo, top_n=5)

    assert len(stats) > 0
    assert stats[0]["file"]
    assert stats[0]["total_commits"] > 0
    assert "total_additions" in stats[0]
    assert "total_deletions" in stats[0]
    assert "churn" in stats[0]
    assert "recency_score" in stats[0]
    assert "commit_messages" in stats[0]
    assert "top_authors" in stats[0]


def test_file_health_has_churn_data():
    """验证 churn 数据非零"""
    from services.git_runner import clone_or_pull_repo
    from services.git_stats import get_file_health_stats

    repo = clone_or_pull_repo(REPO_URL)
    stats = get_file_health_stats(repo, top_n=3)

    for s in stats:
        assert s["churn"] > 0, f"{s['file']} 的 churn 应为正数"
        assert s["total_additions"] >= 0
        assert s["total_deletions"] >= 0
        assert s["recency_score"] > 0, f"{s['file']} 的时效分数应为正数"


def test_file_health_has_messages():
    """验证 commit_messages 不为空"""
    from services.git_runner import clone_or_pull_repo
    from services.git_stats import get_file_health_stats

    repo = clone_or_pull_repo(REPO_URL)
    stats = get_file_health_stats(repo, top_n=3)

    for s in stats:
        if s["total_commits"] > 0:
            assert len(s["commit_messages"]) > 0


def test_file_health_no_hardcoded_paths():
    """验证新版不再硬编码文件夹前缀，能检测到所有文件类型"""
    from services.git_runner import clone_or_pull_repo
    from services.git_stats import get_file_health_stats

    repo = clone_or_pull_repo(REPO_URL)
    stats = get_file_health_stats(repo, top_n=50)

    files = [s["file"] for s in stats]
    has_ext = any("extension/" in f for f in files)
    assert has_ext, "应该检测到 extension/ 目录的文件"


def test_file_health_tool_registered():
    """验证 file_health 工具已注册"""
    from services.agent.tools import registry

    tool = registry.get("file_health")
    assert tool is not None
    assert "bug 概率" in tool.description


def test_classify_file_health():
    """验证 LLM 语义分类不崩溃"""
    from services.llm_service import LLMService

    llm = LLMService()
    sample = [
        {"file": "src/main.py", "total_commits": 5, "commit_messages": [
            "fix: null pointer in login flow",
            "add user profile page",
            "refactor auth middleware",
        ]},
        {"file": "src/utils.py", "total_commits": 3, "commit_messages": [
            "update config parser",
            "chore: bump dependencies",
        ]},
    ]
    result = llm.classify_file_health(sample)
    assert len(result) == 2
    for r in result:
        assert "semantic_bug_probability" in r
        assert "risk_level" in r
        assert r["risk_level"] in ("low", "medium", "high")
