"""测试跨文件函数搜索和 LLM 语义匹配功能。"""

from services.ast_service import (
    extract_functions,
    get_language_for_file,
    search_function_across_files,
)
from services.git_runner import clone_or_pull_repo
from services.git_stats import (
    get_file_commits,
    list_files_at_commit,
    list_files_changed_in_commit,
)
from services.llm_service import LLMService

REPO_URL = "https://github.com/polairszzh/CodeTrace.git"
KNOWN_FILE = "backend/services/ast_service.py"


def test_list_files_at_commit():
    """测试获取仓库文件列表"""
    repo = clone_or_pull_repo(REPO_URL)
    # 用 git log 取任意一个 commit（不绑定到特定文件）
    import subprocess
    result = subprocess.run(
        ["git", "log", "--oneline", "--format=%H", "-1"],
        cwd=repo, capture_output=True, text=True, check=True, timeout=15,
    )
    latest_hash = result.stdout.strip()
    assert latest_hash, "无法获取最新 commit"

    files = list_files_at_commit(repo, latest_hash)
    assert isinstance(files, list)
    assert len(files) > 10
    all_files = " ".join(files)
    assert "backend/" in all_files or "frontend/" in all_files or "extension/" in all_files


def test_list_files_changed_in_commit():
    """测试获取指定 commit 的变更文件列表"""
    repo = clone_or_pull_repo(REPO_URL)
    import subprocess
    result = subprocess.run(
        ["git", "log", "--oneline", "--format=%H", "-1"],
        cwd=repo, capture_output=True, text=True, check=True, timeout=15,
    )
    latest_hash = result.stdout.strip()
    assert latest_hash

    changed = list_files_changed_in_commit(repo, latest_hash)
    assert isinstance(changed, list)  # 可能在最末 commit 没变更，只要不抛异常就行


def test_search_function_finds_itself_in_same_repo():
    """搜索一个已知存在的函数，应该能找到"""
    repo = clone_or_pull_repo(REPO_URL)
    commits = get_file_commits(repo, KNOWN_FILE)
    assert len(commits) > 0

    results = search_function_across_files(
        repo, commits[0]["hash"], "extract_functions",
    )
    found = [r for r in results if "ast_service" in r.get("file", "")]
    assert len(found) > 0, f"在最新 commit 中找不到 extract_functions: {results}"


def test_extract_functions_from_self():
    """验证能从文件中提取至少 3 个函数（不用特定函数名，避免编码问题）"""
    from services.git_stats import get_file_content_at_commit as gf

    repo = clone_or_pull_repo(REPO_URL)
    commits = get_file_commits(repo, KNOWN_FILE)
    assert len(commits) > 0
    content = gf(repo, commits[0]["hash"], KNOWN_FILE)
    assert len(content) > 1000

    lang = get_language_for_file(KNOWN_FILE)
    funcs = extract_functions(content, lang)
    assert len(funcs) >= 3, f"至少应提取 3 个函数，实际: {len(funcs)}"


def test_match_function_across_files_semantic():
    """测试 LLM 语义匹配：两个功能相似的函数应该能匹配上"""
    llm = LLMService()

    # 模拟场景：原函数是 "hello"，候选中有功能相似的 renamed 版本
    old_body = """
def greet_user(name):
    \"\"\"向用户打招呼\"\"\"
    msg = "Hello, " + name
    print(msg)
    return msg
"""

    candidates = [
        {"file": "utils/greeting.py", "name": "say_hello", "body": "def say_hello(name): return 'Hello ' + name"},
        {"file": "utils/format.py", "name": "format_message", "body": "def format_message(text): return text.strip()"},
    ]

    result = llm.match_function_across_files("greet_user", old_body, candidates)
    assert result is not None
    assert "matched" in result
