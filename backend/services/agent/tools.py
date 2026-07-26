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
    get_file_health_stats,
    get_recent_commit_groups,
    get_co_change_trends,
    get_file_change_context,
)
from services.ast_service import trace_function_across_commits, extract_functions, get_language_for_file
from services.github_service import GitHubClient
from services.llm_service import LLMService


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
    name="file_health",
    description="升级版健康度检测：扫描仓库热点文件，返回真实 churn（新增/删除行数）、时效加权分数、commit messages。再结合 LLM 语义分析给出 bug 概率和风险等级。比 repo_health 更精确，推荐优先使用。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "top_n": {"type": "integer", "description": "返回前 N 个文件，默认 20"},
        },
        "required": ["repo_url"],
    },
    execute=lambda repo_url, top_n=20: _analyze_file_health(
        clone_or_pull_repo(repo_url), top_n
    ),
))

def _analyze_file_health(repo_path, top_n=20):
    """执行升级版健康度分析：churn 数据 + LLM 语义分类"""
    stats = get_file_health_stats(repo_path, top_n)
    if not stats:
        return stats
    llm = LLMService()
    return llm.classify_file_health(stats)

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

def _list_functions_at_latest(repo_url: str, file_path: str) -> list[str]:
    repo_path = clone_or_pull_repo(repo_url)
    content = get_file_content_at_commit(repo_path, "HEAD", file_path)
    funcs = extract_functions(content, get_language_for_file(file_path))
    return [f["name"] for f in funcs]


registry.register(Tool(
    name="list_functions",
    description="列出某个文件中当前存在的所有函数名。在 trace_function 之前使用，避免猜测不存在的函数名。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "file_path": {"type": "string", "description": "文件相对路径"},
        },
        "required": ["repo_url", "file_path"],
    },
    execute=lambda repo_url, file_path: _list_functions_at_latest(repo_url, file_path),
))

registry.register(Tool(
    name="analyze_refactor",
    description="检测最近 commit 中的跨文件重构事件。分析多个 commit 的变更文件组，识别哪些是同一重构行为（如模块拆分、重命名、架构迁移）。返回合并后的重构事件列表。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "count": {"type": "integer", "description": "分析最近 N 个 commit，默认 15"},
        },
        "required": ["repo_url"],
    },
    execute=lambda repo_url, count=15: _detect_refactors(
        clone_or_pull_repo(repo_url), count
    ),
))


def _detect_refactors(repo_path, count=15):
    """检测跨文件重构事件"""
    from services.llm_service import LLMService

    groups = get_recent_commit_groups(repo_path, count)
    if not groups:
        return []
    llm = LLMService()
    return llm.detect_refactor_events(groups)


registry.register(Tool(
    name="coupling_risk",
    description="检测仓库中文件耦合关系的变化趋势。比较最近 30 天与上一 30 天的 co-change 数据，识别哪些模块的耦合面正在扩大，是否存在跨模块耦合侵蚀。返回风险文件列表及改进建议。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
        },
        "required": ["repo_url"],
    },
    execute=lambda repo_url: _analyze_coupling(clone_or_pull_repo(repo_url)),
))


def _analyze_coupling(repo_path):
    from services.llm_service import LLMService

    trends = get_co_change_trends(repo_path, window_days=30)
    if not trends:
        return {"coupling_risk": [], "note": "数据不足，仓库活跃度较低或提交历史不够长"}
    llm = LLMService()
    trends = llm.analyze_coupling_trends(trends)
    high_risk = [t for t in trends if t.get("risk") == "high"]
    return {
        "total_files": len(trends),
        "high_risk_count": len(high_risk),
        "coupling_risk": trends,
        "note": "growth > 0.8 且绝对增量 >= 3 表示耦合面显著扩大",
    }


registry.register(Tool(
    name="explain_changes",
    description="获取文件最近变更的详细分析。综合 commit message 和代码 diff，解释每个变更的原因、目的和改动量。不只是看改了啥，而是理解为什么改。",
    parameters={
        "type": "object",
        "properties": {
            "repo_url": {"type": "string", "description": "git 仓库 URL"},
            "file_path": {"type": "string", "description": "文件相对路径"},
            "count": {"type": "integer", "description": "分析最近 N 个 commit，默认 10"},
        },
        "required": ["repo_url", "file_path"],
    },
    execute=lambda repo_url, file_path, count=10: _explain_changes(
        clone_or_pull_repo(repo_url), file_path, count
    ),
))


def _explain_changes(repo_path, file_path, count=10):
    from services.llm_service import LLMService

    contexts = get_file_change_context(repo_path, file_path, count)
    if not contexts:
        return {"changes": [], "note": "该文件没有足够的变更记录"}
    llm = LLMService()
    return {"changes": llm.explain_change_reason(contexts)}