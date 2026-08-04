import os

from services.github_service import GitHubClient

client = GitHubClient(token=os.getenv("GITHUB_TOKEN", ""))


def test_extract_pr_number_found():
    result = client.extract_pr_number("fix: resolve timeout issue (#42)")
    assert result == 42


def test_extract_pr_number_not_found():
    result = client.extract_pr_number("fix: resolve timeout issue")
    assert result is None


def test_extract_pr_number_empty_string():
    result = client.extract_pr_number("")
    assert result is None


def test_extract_pr_number_multiple_numbers():
    result = client.extract_pr_number("fix: issue (#42) and (#43)")
    assert result == 42


def test_extract_pr_number_number_only():
    result = client.extract_pr_number("#42")
    assert result is None

def test_get_pr_info():
      info = client.get_pr_info("polairszzh/CodeTrace", 2)
      assert info is not None
      assert info["title"] == "feat: implement core services and project setup"
      assert info["state"] == "closed"
      assert info["author"] == "polairszzh"


def test_get_pr_discussion():
    comments = client.get_pr_discussion("polairszzh/CodeTrace", 2)
    # PR #2 可能有也可能没有评论，但 API 调用应该是成功的
    assert isinstance(comments, list)