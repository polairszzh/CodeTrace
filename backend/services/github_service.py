import re
import httpx

from services.index_service import get_cached_pr, set_cached_pr

GITHUB_API_URL = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str = ""):
        self.headers = {"Accept": "application/vnd.github+json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def extract_pr_number(self, commit_message: str) -> int | None:
        """
        从提交信息中提取 PR 编号。

        Args:
            commit_message (str): 提交信息。
        
        Returns:
            int | None: 如果找到 PR 编号，返回其整数值；否则返回 None。
        """
        match = re.search(r"\(#(\d+)\)", commit_message)
        if match:
            return int(match.group(1))
        return None
    
    def get_pr_info(self, repo_full: str, pr_number: int) -> dict | None:
        """
        获取指定 PR 的信息。

        Args:
            repo_full (str): 仓库的完整名称，例如 "owner/repo"。
            pr_number (int): PR 编号。

        Returns:
            dict | None: 如果找到 PR，返回其信息字典；否则返回 None。
        """
        # 索引缓存优先，避免重复请求 GitHub API
        cached = get_cached_pr(repo_full, pr_number)
        if cached is not None:
            return cached

        url = f"{GITHUB_API_URL}/repos/{repo_full}/pulls/{pr_number}"
        try:
            resp = httpx.get(url, headers=self.headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            result = {
                "title": data.get("title", ""),
                "body": data.get("body", ""),
                "state": data.get("state", ""),
                "author": data.get("user", {}).get("login", ""),
                "created_at": data.get("created_at", ""),
            }
            set_cached_pr(repo_full, pr_number, result)
            return result
        except Exception as e:
            return None
        
    def get_pr_discussion(self, repo_full: str, pr_number: int) -> list[dict]:
        """
        获取指定 PR 的讨论信息，包括评论和审查。

        Args:
            repo_full (str): 仓库的完整名称，例如 "owner/repo"。
            pr_number (int): PR 编号。

        Returns:
            list[dict]: PR 讨论信息列表，每个元素包含评论或审查的详细信息。
        """
        url = f"{GITHUB_API_URL}/repos/{repo_full}/pulls/{pr_number}/comments"
        try:
            resp = httpx.get(url, headers=self.headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return [
                {
                    "author": c.get("user", {}).get("login", ""),
                    "body": c.get("body", ""),
                    "created_at": c.get("created_at", ""),
                }
                for c in data
            ]
        except Exception as e:
            return []
