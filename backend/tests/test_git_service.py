from services.git_service import clone_or_pull_repo, get_file_commits, get_commit_diff_stats

def test_clone_and_get_commits(tmp_path):
    # 用 CodeTrace 自己的仓库来测试
    repo_path = clone_or_pull_repo("https://github.com/polairszzh/CodeTrace.git")
    assert repo_path.exists()

    # 拿 .gitignore 文件的提交记录来测试
    commits = get_file_commits(repo_path, ".gitignore")
    assert len(commits) > 0
    assert commits[0]["hash"]
    assert commits[0]["author"]
    assert commits[0]["date"]
    assert commits[0]["message"]

def test_get_commit_diff_stats():
    # 用 CodeTrace 自己的仓库来测试
    repo_path = clone_or_pull_repo("https://github.com/polairszzh/CodeTrace.git")
    commits = get_file_commits(repo_path, ".gitignore")
    assert len(commits) > 0

    stats = get_commit_diff_stats(repo_path, commits[0]["hash"])
    assert "additions" in stats
    assert "deletions" in stats
    assert "files_changed" in stats