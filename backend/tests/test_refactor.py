"""测试跨文件重构感知"""

REPO_URL = "https://github.com/polairszzh/CodeTrace.git"


def test_get_recent_commit_groups_structure():
    """验证 commit 分组返回正确结构"""
    from services.git_service import get_recent_commit_groups, clone_or_pull_repo

    repo = clone_or_pull_repo(REPO_URL)
    groups = get_recent_commit_groups(repo, count=5)

    assert len(groups) > 0
    assert groups[0]["commit_hash"]
    assert groups[0]["message"]
    assert groups[0]["file_count"] > 0
    assert groups[0]["total_churn"] > 0
    assert "files" in groups[0]
    assert "author" in groups[0]
    assert "date" in groups[0]


def test_commit_group_file_detailed():
    """验证每个 commit 的文件包含 path, additions, deletions"""
    from services.git_service import get_recent_commit_groups, clone_or_pull_repo

    repo = clone_or_pull_repo(REPO_URL)
    groups = get_recent_commit_groups(repo, count=3)

    for g in groups:
        for f in g["files"]:
            assert "path" in f
            assert "additions" in f
            assert "deletions" in f
            # 路径应该是完整路径
            assert "/" in f["path"] or "." in f["path"]


def test_analyze_refactor_tool_registered():
    """验证 analyze_refactor 工具已注册"""
    from services.agent.tools import registry

    tool = registry.get("analyze_refactor")
    assert tool is not None
    assert "重构" in tool.description or "refactor" in tool.description


def test_detect_refactor_events_llm():
    """验证 LLM 重构检测不崩溃"""
    from services.llm_service import LLMService

    llm = LLMService()
    sample = [
        {
            "commit_hash": "abc123",
            "author": "dev",
            "date": "2026-07-20",
            "message": "refactor: extract auth middleware into separate module",
            "file_count": 4,
            "total_churn": 350,
            "files": [
                {"path": "backend/main.py", "additions": 10, "deletions": 80},
                {"path": "backend/auth/middleware.py", "additions": 120, "deletions": 0},
                {"path": "backend/auth/__init__.py", "additions": 5, "deletions": 0},
                {"path": "backend/routers/api.py", "additions": 20, "deletions": 30},
            ],
        },
        {
            "commit_hash": "def456",
            "author": "dev",
            "date": "2026-07-19",
            "message": "fix typo in readme",
            "file_count": 1,
            "total_churn": 2,
            "files": [
                {"path": "README.md", "additions": 1, "deletions": 1},
            ],
        },
    ]
    result = llm.detect_refactor_events(sample)
    assert isinstance(result, list)
    # 第一个 commit 应该是重构，至少有一个结果
    if result:
        assert "summary" in result[0]
        assert "refactor_type" in result[0]
