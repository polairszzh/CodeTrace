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


def list_files_at_commit(repo_path: Path, commit_hash: str) -> list[str]:
    """获取指定 commit 的仓库文件列表"""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit_hash],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8",
        check=True, timeout=30,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


def list_files_changed_in_commit(repo_path: Path, commit_hash: str) -> list[str]:
    """获取指定 commit 中变更的文件列表（缩小搜索范围用）"""
    result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", commit_hash],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8",
        check=True, timeout=15,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


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
    """旧版——保留向后兼容。推荐使用 get_file_health_stats。"""
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


def get_file_health_stats(repo_path: Path, top_n: int = 20) -> list[dict]:
    """
    升级版健康度检测：使用 git log --numstat 获取真实 churn 数据，
    移除目录硬编码过滤，加入时效加权。

    返回每个文件的：
    - file: 文件路径
    - total_commits: 总提交次数
    - total_additions: 总新增行数
    - total_deletions: 总删除行数
    - churn: 总变更行数 (additions + deletions)
    - recency_score: 时效分数（值越大表示近期越活跃）
    - commit_messages: 最近 commit message 列表（供 LLM 语义分类）
    - top_authors: 主要贡献者
    """
    from collections import defaultdict
    import datetime

    # git log --numstat 输出格式：
    # additions\tdeletions\tfilepath
    # 每个 commit 前有一行 commit-head 信息
    result = subprocess.run(
        ["git", "log", "--numstat", "--pretty=format:__COMMIT__%H|%an|%ai|%s", "--reverse"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    file_data = defaultdict(lambda: {
        "commits": 0, "additions": 0, "deletions": 0,
        "messages": [], "authors": set(), "dates": [],
    })

    current_commit = None
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        if line.startswith("__COMMIT__"):
            # 新 commit 开始
            parts = line[9:].split("|", 3)
            current_commit = {
                "hash": parts[0] if len(parts) > 0 else "",
                "author": parts[1] if len(parts) > 1 else "",
                "date": parts[2] if len(parts) > 2 else "",
                "message": parts[3] if len(parts) > 3 else "",
                "seen_files": set(),
            }
        elif current_commit and "\t" in line:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            add_str, del_str, filepath = parts
            if add_str == "-" or del_str == "-":
                continue  # 二进制文件

            try:
                add_val = int(add_str) if add_str != "" else 0
                del_val = int(del_str) if del_str != "" else 0
            except ValueError:
                continue

            fd = file_data[filepath]
            fd["commits"] += 1
            fd["additions"] += add_val
            fd["deletions"] += del_val
            fd["authors"].add(current_commit["author"])
            fd["dates"].append(current_commit["date"])
            if current_commit["message"] and len(fd["messages"]) < 5:
                fd["messages"].append(current_commit["message"])

    now = datetime.datetime.now()
    stats = []
    for filepath, fd in file_data.items():
        if fd["commits"] == 0:
            continue

        # 时效加权：最近 30 天内的 commit 权重大
        recency_score = 0
        for d in fd.get("dates", []):
            try:
                dt = datetime.datetime.strptime(d[:10], "%Y-%m-%d")
                days_ago = (now - dt).days
                if days_ago <= 7:
                    recency_score += 10
                elif days_ago <= 30:
                    recency_score += 5
                elif days_ago <= 90:
                    recency_score += 2
                else:
                    recency_score += 0.5
            except Exception:
                recency_score += 0.5

        stats.append({
            "file": filepath,
            "total_commits": fd["commits"],
            "total_additions": fd["additions"],
            "total_deletions": fd["deletions"],
            "churn": fd["additions"] + fd["deletions"],
            "recency_score": round(recency_score, 1),
            "commit_messages": list(fd["messages"]),
            "top_authors": list(fd["authors"])[:3],
        })

    # 按 churn + recency 综合排序
    stats.sort(key=lambda x: x["churn"] * x["recency_score"], reverse=True)
    return stats[:top_n]

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


def get_recent_commit_groups(repo_path: Path, count: int = 15) -> list[dict]:
    """
    获取最近 N 个 commit 的变更文件组和 diff 行数摘要。

    输出给 LLM 判断：哪些 commit 是跨文件重构事件（一次重构改了多个文件），
    哪些是独立变更。

    Returns:
        list[dict]: 每项含 commit_hash, author, date, message,
                    file_count, total_churn, files (列表，含 path, additions, deletions)
    """
    result = subprocess.run(
        ["git", "log", "--numstat", "--pretty=format:__COMMIT__%H|%an|%ai|%s", f"-{count}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    groups = []
    current = None
    current_files = []

    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("__COMMIT__"):
            if current:
                current["files"] = current_files
                current["file_count"] = len(current_files)
                current["total_churn"] = sum(f.get("additions", 0) + f.get("deletions", 0) for f in current_files)
                groups.append(current)

            parts = line[9:].split("|", 3)
            current = {
                "commit_hash": parts[0] if len(parts) > 0 else "",
                "author": parts[1] if len(parts) > 1 else "",
                "date": parts[2] if len(parts) > 2 else "",
                "message": parts[3] if len(parts) > 3 else "",
            }
            current_files = []
        elif current and "\t" in line:
            parts = line.split("\t")
            if len(parts) == 3:
                add_s, del_s, path = parts
                if add_s != "-" and del_s != "-":
                    try:
                        current_files.append({
                            "path": path,
                            "additions": int(add_s) if add_s else 0,
                            "deletions": int(del_s) if del_s else 0,
                        })
                    except ValueError:
                        pass

    if current:
        current["files"] = current_files
        current["file_count"] = len(current_files)
        current["total_churn"] = sum(f.get("additions", 0) + f.get("deletions", 0) for f in current_files)
        groups.append(current)

    return groups


def get_co_change_trends(repo_path: Path, window_days: int = 30) -> list[dict]:
    """
    双窗口 co-change 趋势检测：比较近期与历史时期文件耦合关系的变化。

    分别对最近 window_days 天（近期窗口）和前一个 window_days 天（历史窗口）
    计算文件的 co-change 矩阵，然后对比两个窗口的指标变化。

    Args:
        repo_path: 仓库本地路径。
        window_days: 单个窗口天数，默认 30。

    Returns:
        list[dict]: 按风险排序的列表，每项含:
            - file: 文件路径
            - recent_partners: 近期窗口中共变伙伴数
            - old_partners: 历史窗口中共变伙伴数
            - coupling_growth: 伙伴数增长率
            - boundary_crossings: 近期跨模块共现次数
            - risk: 预置风险标签
    """
    from collections import defaultdict, Counter
    import datetime

    now = datetime.datetime.now()

    def window_commits(since_days: int, until_days: int) -> list[set[str]]:
        since = (now - datetime.timedelta(days=since_days)).strftime("%Y-%m-%d")
        until = (now - datetime.timedelta(days=until_days)).strftime("%Y-%m-%d")
        result = subprocess.run(
            ["git", "log", "--name-only", "--pretty=format:", "--since", since, "--until", until],
            cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        groups = []
        current_set = set()
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                if current_set:
                    groups.append(current_set)
                    current_set = set()
                continue
            current_set.add(line)
        if current_set:
            groups.append(current_set)
        return groups

    recent_groups = window_commits(window_days, 0)
    old_groups = window_commits(window_days * 2, window_days)

    def build_metrics(groups):
        partner_map = defaultdict(set)
        bx_counter = Counter()
        for files in groups:
            flist = list(files)
            for i, fa in enumerate(flist):
                for fb in flist[i + 1:]:
                    partner_map[fa].add(fb)
                    partner_map[fb].add(fa)
                    ma = fa.replace("\\", "/").split("/")[0] if "/" in fa or "\\" in fa else ""
                    mb = fb.replace("\\", "/").split("/")[0] if "/" in fb or "\\" in fb else ""
                    if ma and mb and ma != mb:
                        bx_counter[fa] += 1
                        bx_counter[fb] += 1
        return {f: {"partners": len(p), "bx": bx_counter.get(f, 0)} for f, p in partner_map.items()}

    recent_m = build_metrics(recent_groups)
    old_m = build_metrics(old_groups)

    all_files = set(recent_m.keys()) | set(old_m.keys())
    results = []
    for f in all_files:
        rec = recent_m.get(f, {"partners": 0, "bx": 0})
        old = old_m.get(f, {"partners": 0, "bx": 0})
        if rec["partners"] == 0 and old["partners"] == 0:
            continue
        old_p = old["partners"] or 1
        growth = round((rec["partners"] - old["partners"]) / old_p, 2)
        results.append({
            "file": f,
            "recent_partners": rec["partners"],
            "old_partners": old["partners"],
            "coupling_growth": growth,
            "boundary_crossings": rec["bx"],
            "risk": "high" if growth > 0.5 else ("medium" if growth > 0.2 else "low"),
        })

    results.sort(key=lambda x: (-x["coupling_growth"], -x["boundary_crossings"]))
    return results[:30]


def get_file_change_context(repo_path: Path, file_path: str, count: int = 10) -> list[dict]:
    """
    获取某文件最近 N 个 commit 的变更上下文：message + diff 摘要。

    Args:
        repo_path: 仓库路径。
        file_path: 文件相对路径。
        count: 最近 commit 数，默认 10。

    Returns:
        list[dict]: 每项含 commit_hash, author, date, message, diff_summary, files_changed。
    """
    result = subprocess.run(
        ["git", "log", f"-{count}", "--format=%H|%an|%ai|%s", "--", file_path],
        cwd=repo_path, capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
    )

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue

        commit_hash = parts[0]
        author = parts[1]
        date = parts[2]
        message = parts[3]

        # 获取该 commit 的 diff 统计和详细摘要
        try:
            diff_result = subprocess.run(
                ["git", "diff", f"{commit_hash}~1", commit_hash, "--", file_path,
                 "--unified=3", "--no-color"],
                cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=15,
            )
            diff_text = diff_result.stdout
        except Exception:
            diff_text = ""

        # 精简 diff 到 1000 字符
        diff_short = diff_text[:1000]
        if len(diff_text) > 1000:
            diff_short += "\n... (diff truncated)"

        commits.append({
            "commit_hash": commit_hash[:8],
            "author": author,
            "date": date[:10],
            "message": message,
            "diff_summary": diff_short,
        })

    return commits