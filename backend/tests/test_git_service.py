from services.git_service import clone_or_pull_repo, get_file_commits

def test_clone_and_get_commits(tmp_path):
    # 用 CodeTrace 自己的仓库来测试
    repo_path = clone_or_pull_repo("https://github.com/polairszzh/CodeTrace.git")
    assert repo_path.exists()

    # 拿 bcakend/services/git