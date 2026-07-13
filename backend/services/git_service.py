import subprocess
from pathlib import Path

CACHE_DIR = Path("./tmp/codetrace")


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