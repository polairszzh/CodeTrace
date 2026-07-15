import os

from services.agent.tool_registry import Tool, ToolRegistry
from services.git_service import (
    clone_or_pull_repo,
    get_file_commits,
    get_file_content_at_commit,
    get_commit_diff_stats,
    get_top_changed_files,
    get_repo_health_stats,
    get_file_bulk_summary,
)
from services.ast_service import trace_function_across_commits, extract_functions
from services.github_service import GitHubClient


github = GitHubClient(token=os.getenv("GITHUB_TOKEN", ""))

registry = ToolRegistry()

def _extract_repo_full(repo_url: str) -> str:
    """从 URL 提取 owner/repo"""
    parts = repo_url.rstrip("/").split("/")
    return f"{parts[-2]}/{parts[-1].replace('.git', '')}"


registry.register(Tool(
    name="pr_info",
    description="根据 commit message（需含 PR 编号如 #42）和仓库 URL，获取对应 PR 的标题、描述和状态。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "commit_message": {"type": "string", "description": "commit message，格式需包含 (#数字)"},
        },
        "required": ["repo_url", "commit_message"],
    },
    execute=lambda repo_url, commit_message: (
        pr := github.get_pr_info(
            _extract_repo_full(repo_url),
            github.extract_pr_number(commit_message)
        )
    ) if github.extract_pr_number(commit_message) else None,
))

registry.register(Tool(
    name="git_log",
    description="获取某个仓库中某个文件的所有 commit 历史。返回列表：每个元素含 hash、author、date、message。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL，如 https://github.com/owner/repo.git"},
            "file_path": {"type": "string", "description": "文件相对路径，如 src/main.py"},
        },
        "required": ["repo_url", "file_path"],
    },
    execute=lambda repo_url, file_path: get_file_commits(
        clone_or_pull_repo(repo_url), file_path
    ),
))

registry.register(Tool(
    name="git_hotspots",
    description="扫描整个仓库，返回变更频率最高的前 N 个文件。用于发现项目的热点模块。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "top_n": {"type": "integer", "description": "返回前 N 个文件，默认 10"},
        },
        "required": ["repo_url"],
    },
    execute=lambda repo_url, top_n=10: get_top_changed_files(
        clone_or_pull_repo(repo_url), top_n
    ),
))

registry.register(Tool(
    name="trace_function",
    description="追踪某个函数在文件变更历史中的完整演变过程。返回每个 commit 中该函数的状态（名称、行号、函数体）。可发现函数是否被重命名、拆分或合并。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "file_path": {"type": "string", "description": "文件相对路径"},
            "function_name": {"type": "string", "description": "要追踪的函数名"},
        },
        "required": ["repo_url", "file_path", "function_name"],
    },
    execute=lambda repo_url, file_path, function_name: trace_function_across_commits(
        clone_or_pull_repo(repo_url), file_path, function_name
    ),
))

registry.register(Tool(
    name="repo_health",
    description="扫描整个仓库，返回每个高频变更文件的详细统计：总变更次数、bug 修复次数、bug 修复占比。用于评估代码健康度和模块风险。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "top_n": {"type": "integer", "description": "返回前 N 个文件，默认 10"},
        },
        "required": ["repo_url"],
    },
    execute=lambda repo_url, top_n=10: get_repo_health_stats(
        clone_or_pull_repo(repo_url), top_n
    ),
))

registry.register(Tool(
    name="file_bulk_summary",
    description="批量获取多个文件的概要信息：每个文件的 commit 总数、最近修改日期、主要贡献者列表。当需要快速了解一批文件的活跃程度时使用。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "文件路径列表，如 ['src/main.py', 'src/utils.py']",
            },
        },
        "required": ["repo_url", "file_paths"],
    },
    execute=lambda repo_url, file_paths: get_file_bulk_summary(
        clone_or_pull_repo(repo_url), file_paths
    ),
))