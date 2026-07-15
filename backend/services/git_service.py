import os
import subprocess
from pathlib import Path

CACHE_DIR = Path(os.getenv("CODETRACE_CACHE", "/tmp/codetrace"))


def clone_or_pull_repo(repo_url: str) -> Path:
    """
    Clone the repository if it doesn't exist, or pull the latest changes if it does.

    Args:
        repo_url (str): The URL of the Git repository.

    Returns:
        Path: The path to the cloned or updated repository.
    """
    # Extract the repository name from the URL
    # Example: https://github.com/polairszzh/CodeTrace.git -> CodeTrace
    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    repo_path = CACHE_DIR / repo_name

    # if the repository doesn't exist, clone it
    if not repo_path.exists():
        subprocess.run(["git", "clone", "--depth=50", repo_url, str(repo_path)], check=True, timeout=120)
    else:
        subprocess.run(["git", "pull"], cwd=repo_path, check=True, timeout=30)

    return repo_path

def get_file_commits(repo_path: Path, file_path: str) -> list[dict]:
    """
    Get the commit history for a specific file in the repository.

    Args:
        repo_path (Path): The path to the cloned repository.
        file_path (str): The relative path to the file within the repository.

    Returns:
        list[dict]: A list of commit info dicts with keys: hash, author, date, message.
    """
    # Use git log to get the commit history for the specific file
    result = subprocess.run(
        ["git", "log", "--follow", "--format=%H|%an|%ai|%s", "--", file_path],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30
    )

    # Parse the output into structured dicts
    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 3)
        commits.append({
            "hash": parts[0],
            "author": parts[1],
            "date": parts[2],
            "message": parts[3],
        })
    return commits


def get_commit_diff_stats(repo_path: Path, commit_hash: str) -> dict:
    """
    获取指定提交的差异统计信息，包括新增行数、删除行数和修改行数。

    Args:
        repo_path (Path): 仓库的路径。
        commit_hash (str): 提交的哈希值。
    
    Returns:
        dict: {"additions": int, "deletions": int, "files_changed": int}
    """
    result = subprocess.run(
        ["git", "show", "--stat", "--format=", commit_hash],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )

    additions = 0
    deletions = 0
    files_changed = 0

    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        files_changed += 1
        # 解析 "1 file changed, 2 insertions(+), 1 deletion(-)" 这种格式
        if "insertion" in line or "deletion" in line:
            # 简单按逗号分割，然后提取数字
            for part in line.split(","):
                part = part.strip()
                if "insertion" in part:
                    additions += int(part.split()[0])
                elif "deletion" in part:
                    deletions += int(part.split()[0])

    return {
        "additions": additions,
        "deletions": deletions,
        "files_changed": files_changed,
    }

def get_file_content_at_commit(repo_path: Path, commit_hash: str, file_path: str) -> str:
    """
    获取指定提交中某个文件的内容。

    Args:
        repo_path (Path): 仓库的路径。
        commit_hash (str): 提交的哈希值。
        file_path (str): 文件在仓库中的相对路径。
    
    Returns:
        str: 文件内容的字符串。
    """
    result = subprocess.run(
        ["git", "show", f"{commit_hash}:{file_path}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=15,
    )
    return result.stdout

def get_top_changed_files(repo_path: Path, top_n: int = 10) -> list[str]:
      """扫描整个仓库，返回变更频率最高的前 N 个文件路径。"""
      from collections import Counter

      result = subprocess.run(
          ["git", "log", "--pretty=format:", "--name-only"],
          cwd=repo_path,
          capture_output=True,
          text=True,
          encoding="utf-8",
          timeout=30,
      )
      counter = Counter()
      for line in result.stdout.strip().split("\n"):
          line = line.strip()
          if line:
              counter[line] += 1
      return [f for f, _ in counter.most_common(top_n)]

def get_repo_health_stats(repo_path: Path, top_n: int = 10) -> list[dict]:
    """
    扫描仓库，返回每个文件的变更统计（变更次数、bug 修复次数、最近活跃情况）。
    用于 Agent 生成代码健康度报告。
    """
    from collections import Counter

    result = subprocess.run(
        ["git", "log", "--pretty=format:%s", "--name-only"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )

    lines = result.stdout.strip().split("\n")
    file_counter = Counter()
    bug_counter = Counter()
    current_message = ""

    for line in lines:
        line = line.strip()
        if not line:
            current_message = ""
            continue
        if not line.startswith("backend/") and not line.startswith("frontend/") and not line.startswith("extension/"):
            current_message = line
            continue
        file_counter[line] += 1
        # 简单启发式：包含 fix/bug/hotfix/patch 的 message 标记为 bug 修复
        msg_lower = current_message.lower()
        if any(kw in msg_lower for kw in ("fix", "bug", "hotfix", "patch", "修复", "resolve", "issue")):
            bug_counter[line] += 1

    stats = []
    for filepath, total in file_counter.most_common(top_n):
        bugs = bug_counter.get(filepath, 0)
        stats.append({
            "file": filepath,
            "total_changes": total,
            "bug_fixes": bugs,
            "bug_ratio": round(bugs / total, 2) if total > 0 else 0,
        })
    return stats

def get_file_bulk_summary(repo_path: Path, file_paths: list[str]) -> list[dict]:
    """
    批量获取多个文件的基本信息：每个文件的 commit 数量、最近修改日期、主要贡献者。
    用于 Agent 快速了解多个热点文件的活跃程度。
    """
    summaries = []
    for fp in file_paths:
        try:
            commits = get_file_commits(repo_path, fp)
            authors = list({c["author"] for c in commits[:20]})
            summaries.append({
                "file": fp,
                "total_commits": len(commits),
                "latest_date": commits[0]["date"] if commits else None,
                "top_authors": authors[:5],
            })
        except Exception:
            summaries.append({"file": fp, "error": "无法读取"})
    return summaries