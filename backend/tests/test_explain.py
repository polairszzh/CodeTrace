"""测试变更原因语义理解"""

REPO_URL = "https://github.com/polairszzh/CodeTrace.git"
KNOWN_FILE = "backend/services/git_service.py"


def test_get_file_change_context_structure():
    """验证变更上下文数据返回正确结构"""
    from services.git_service import get_file_change_context, clone_or_pull_repo

    repo = clone_or_pull_repo(REPO_URL)
    ctx = get_file_change_context(repo, KNOWN_FILE, count=3)

    assert len(ctx) > 0
    assert ctx[0]["commit_hash"]
    assert ctx[0]["message"]
    assert ctx[0]["diff_summary"]
    assert "author" in ctx[0]
    assert "date" in ctx[0]


def test_file_change_context_has_diff():
    """验证每个 commit 包含 diff 摘要"""
    from services.git_service import get_file_change_context, clone_or_pull_repo

    repo = clone_or_pull_repo(REPO_URL)
    ctx = get_file_change_context(repo, KNOWN_FILE, count=3)

    for c in ctx:
        assert len(c["diff_summary"]) > 0
        # diff 应该包含代码变更符号
        assert "diff" in c["diff_summary"].lower() or "+" in c["diff_summary"] or "-" in c["diff_summary"]


def test_explain_changes_tool_registered():
    """验证 explain_changes 工具已注册"""
    from services.agent.tools import registry

    tool = registry.get("explain_changes")
    assert tool is not None
    assert "为什么改" in tool.description or "explain" in tool.description.lower()


def test_explain_change_reason_llm():
    """验证 LLM 变更原因分析不崩溃"""
    from services.llm_service import LLMService

    llm = LLMService()
    sample = [
        {"commit_hash": "abc123", "message": "fix: null pointer in login flow",
         "diff_summary": "diff --git a/src/auth.py b/src/auth.py\n@@ -10,3 +10,5 @@\n+if user is None:\n+    return None\n", "author": "dev", "date": "2026-07-20"},
    ]
    result = llm.explain_change_reason(sample)
    assert len(result) == 1
    assert "reason" in result[0]
    assert "why" in result[0]
    assert "effort" in result[0]
