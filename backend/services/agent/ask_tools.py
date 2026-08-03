"""轻量 Agent 工具集 — 用于"问 Agent"对话式入口"""
import os
from services.agent.tool_registry import Tool, ToolRegistry
from services.git_service import (
    clone_or_pull_repo,
    get_file_commits,
    get_commit_diff_content,
    get_file_content_at_commit,
    repo_full_from_url,
)
from services.ast_service import trace_function_across_commits
from services.github_service import GitHubClient

github = GitHubClient(token=os.getenv("GITHUB_TOKEN", ""))

ask_registry = ToolRegistry()


def _extract_repo_full(repo_url: str) -> str:
    """从 URL 提取 owner/repo（https/SSH 通用）"""
    return repo_full_from_url(repo_url) or ""


ask_registry.register(Tool(
    name="get_commit_diff",
    description="获取某次 commit 的完整 diff（代码变更内容）。回答「这次改了啥」时优先使用。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "commit_hash": {"type": "string", "description": "commit 哈希值（完整或前 7 位）"},
        },
        "required": ["repo_url", "commit_hash"],
    },
    execute=lambda repo_url, commit_hash: get_commit_diff_content(
        clone_or_pull_repo(repo_url), commit_hash
    ),
))

ask_registry.register(Tool(
    name="read_file_at_head",
    description="读取仓库中某个文件的当前版本内容。用于了解文件的整体结构和职责。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "file_path": {"type": "string", "description": "文件相对路径"},
        },
        "required": ["repo_url", "file_path"],
    },
    execute=lambda repo_url, file_path: get_file_content_at_commit(
        clone_or_pull_repo(repo_url), "HEAD", file_path
    ),
))

ask_registry.register(Tool(
    name="get_file_commits",
    description="获取某个文件的所有 commit 历史。返回列表：每个元素含 hash、author、date、message。用于了解什么时候改了什么。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "file_path": {"type": "string", "description": "文件相对路径"},
        },
        "required": ["repo_url", "file_path"],
    },
    execute=lambda repo_url, file_path: get_file_commits(
        clone_or_pull_repo(repo_url), file_path
    ),
))

ask_registry.register(Tool(
    name="trace_function",
    description="追踪某个函数在文件变更历史中的完整演变。返回每个 commit 中该函数的状态（行号、函数体）。可发现函数是否被重命名、拆分、合并。",
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

def _get_pr_info(repo_url: str, commit_message: str) -> dict:
    pr_number = github.extract_pr_number(commit_message)
    if not pr_number:
        return {"note": "commit message 中未发现 PR 编号"}
    pr = github.get_pr_info(_extract_repo_full(repo_url), pr_number)
    return pr or {"note": "未获取到 PR 信息"}

ask_registry.register(Tool(
    name="get_pr_info",
    description="通过 commit message 中的 PR 编号（如 #42），获取对应 PR 的标题、描述和状态。理解变更上下文时使用。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "commit_message": {"type": "string", "description": "commit message，需含 (#数字)"},
        },
        "required": ["repo_url", "commit_message"],
    },
    execute=_get_pr_info,
))
