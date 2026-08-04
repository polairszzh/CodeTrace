from services.ast_service import trace_function_across_commits
from services.git_runner import clone_or_pull_repo


def test_trace_function_across_commits():
    repo_path = clone_or_pull_repo("https://github.com/polairszzh/CodeTrace.git")
    result = trace_function_across_commits(
        repo_path,
        "backend/services/git_stats.py",
        "get_file_commits",
    )
    history = result["history"]
    assert len(history) > 0
    assert history[0]["function"]["name"] == "get_file_commits"
    assert history[0]["commit_hash"]
    assert history[0]["author"]
    # migration_path 应为空（函数未迁移时）
    assert len(result.get("migration_path", [])) == 0
