from services.ast_service import trace_function_across_commits
from services.git_service import clone_or_pull_repo


def test_trace_function_across_commits():
    repo_path = clone_or_pull_repo("https://github.com/polairszzh/CodeTrace.git")
    history = trace_function_across_commits(
        repo_path,
        "backend/services/git_service.py",
        "get_file_commits",
    )
    assert len(history) > 0
    assert history[0]["function"]["name"] == "get_file_commits"
    assert history[0]["commit_hash"]
    assert history[0]["author"]
