import os
from fastapi import APIRouter, HTTPException
from models.schemas import TraceRequest, TraceResponse, TimelineNode, DiffStats
from services.git_service import clone_or_pull_repo, get_file_commits, get_commit_diff_stats
from services.github_service import GitHubClient
from services.llm_service import LLMService
from services.ast_service import trace_function_across_commits

router = APIRouter()

github = GitHubClient(token=os.getenv("GITHUB_TOKEN", ""))
llm = LLMService(
    api_key=os.getenv("LLM_API_KEY", ""),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    model=os.getenv("LLM_MODEL", "deepseek-v4-pro")
)

@router.post("/trace", response_model=TraceResponse)
async def trace_file(req: TraceRequest):
    """
    跟踪指定仓库中某个文件的修改历史，并生成时间线。

    Args:
        req (TraceRequest): 包含仓库 URL 和文件路径的请求体。
    
    Returns:
        TraceResponse: 包含时间线和提交统计信息的响应体。
    """
    # 1. 解析 repo_url
    try:
        parts = req.repo_url.rstrip("/").split("/")
        repo_owner, repo_name = parts[-2], parts[-1].replace(".git", "")
        repo_full = f"{repo_owner}/{repo_name}" 
    except Exception:
        raise HTTPException(status_code=400, detail="仓库格式地址错误")
    
    # 2. clone/pull 仓库
    try:
        repo_path = clone_or_pull_repo(req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败: {str(e)}")
    
    # 3. 获取文件 commit 历史
    commits = get_file_commits(repo_path, req.file_path)
    if not commits:
        raise HTTPException(status_code=404, detail="该文件无变更历史")
    
    # 4. 构建 timeline
    timeline = []
    for c in commits:
        pr_number = github.extract_pr_number(c["message"])
        pr_info = None
        if pr_number:
            pr_info = github.get_pr_info(repo_full, pr_number)
        
        diff_stats = get_commit_diff_stats(repo_path, c["hash"])
        llm_result = llm.classify_and_summarize(
            commit_message=c["message"],
            pr_title=pr_info.get("title") if pr_info else None,
            pr_description=pr_info.get("body") if pr_info else None
        )

        node = TimelineNode(
            commit_hash=c["hash"],
            author=c["author"],
            date=c["date"],
            message=c["message"],
            pr_number=pr_number,
            pr_title=pr_info.get("title") if pr_info else None,
            change_type=llm_result.get("change_type", "chore"),
            summary=llm_result.get("summary", c["message"][:50]),
            diff_stats=DiffStats(**diff_stats)
        )
        timeline.append(node)

    return TraceResponse(
        repo=repo_full,
        file_path=req.file_path,
        timeline=timeline,
        commit_count=len(timeline),
    )

@router.post("/trace/function")
async def trace_function(req: TraceRequest, function_name: str = ""):
    if not function_name:
        raise HTTPException(status_code=400, detail="请提供函数名")
    
    try:
        parts = req.repo_url.rstrip("/").split("/")
        repo_owner, repo_name = parts[-2], parts[-1].replace(".git", "")
        repo_full = f"{repo_owner}/{repo_name}"
    except Exception:
        raise HTTPException(status_code=400, detail="仓库地址格式有误")
    
    try:
        repo_path = clone_or_pull_repo(req.repo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"仓库操作失败：{str(e)}")
    
    history = trace_function_across_commits(repo_path, req.file_path, function_name)

    return {
        "repo": repo_full,
        "file_path": req.file_path,
        "function_name": function_name,
        "history": history,
        "commit_count": len(history),
    }