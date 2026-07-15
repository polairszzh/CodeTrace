import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
from tree_sitter import Language, Parser
from pathlib import Path
from services.git_service import get_file_commits, get_file_content_at_commit
from services.llm_service import LLMService

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())

FUNC_TYPES = {"function_definition", "function_declaration", "arrow_function", "method_definition"}

def get_language_for_file(file_path: str):
    if file_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        return JS_LANGUAGE
    return PY_LANGUAGE


def extract_functions(source_code: str, language=None) -> list[dict]:
    """
    提取源代码中的函数定义。

    Args:
        source_code (str): 源代码字符串。
        language: 待提取代码语言

    Returns:
        list[dict]: 包含函数名称、参数和起始行号的字典列表。
    """
    if language is None:
        language = PY_LANGUAGE
    parser = Parser(language)

    tree = parser.parse(bytes(source_code, "utf8"))
    functions = []

    def walk(node):
        if node.type in FUNC_TYPES:
            name_node = node.child_by_field_name("name")
            body_node = node.child_by_field_name("body")
            if name_node and body_node:
                functions.append({
                    "name": source_code[name_node.start_byte:name_node.end_byte],
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "body": source_code[node.start_byte:node.end_byte],
                })
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return functions

def trace_function_across_commits(repo_path: Path, file_path: str, function_name: str) -> list[dict]:
    """
    追踪一个函数在文件变更历史中的演变。

    遍历文件的所有 commit，在每个版本中用 AST 解析函数定义，
    返回该函数在各个 commit 中的状态列表（按 commit 时间倒序）。

    Args:
        repo_path: 仓库本地缓存路径。
        file_path: 要追溯的文件路径（相对于仓库根目录）。
        function_name: 要追踪的函数名。

    Returns:
        list[dict]: 按时间倒序，每个元素包含:
            - commit_hash: commit 哈希
            - author: 作者
            - date: 提交日期
            - message: commit message
            - function: 该版本中函数的信息（name, start_line, end_line, body）
    """
    commits = get_file_commits(repo_path, file_path)
    history = []
    last_known_body = None
    llm = LLMService() # 读环境变量初始化

    lang = get_language_for_file(file_path)
    for c in commits:
        try:
            content = get_file_content_at_commit(repo_path, c["hash"], file_path)
        except Exception:
            continue    # 该 commit 文件可能还不存在
        
        functions = extract_functions(content, lang)
        matched = [f for f in functions if f["name"] == function_name]

        if matched:
            history.append({
                "commit_hash": c["hash"],
                "author": c["author"],
                "date": c["date"],
                "message": c["message"],
                "function": matched[0],
            })
            last_known_body = matched[0]["body"]
        elif last_known_body and functions:
            # AST 找不到同名函数，尝试 LLM 模糊匹配
            new_names = [f["name"] for f in functions]
            result = llm.trace_function_change(function_name, last_known_body, new_names)
            if result and result.get("action") in ("renamed", "split", "merged"):
                name_found = result.get("new_name", "")
                new_matched = [f for f in functions if f["name"] == name_found]
                if new_matched:
                    history.append({
                        "commit_hash": c["hash"],
                        "author": c["author"],
                        "date": c["date"],
                        "message": c["message"],
                        "function": new_matched[0],
                        "llm_note": result.get("note", f"疑似重命名为 {name_found}"),
                    })
                    last_known_body = new_matched[0]["body"]
                    continue
            history.append({
                "commit_hash": c["hash"],
                "author": c["author"],
                "date": c["date"],
                "message": c["message"],
                "function": None,
                "llm_note": result.get("note", "函数已消失") if result else "函数已消失，LLM 无法定位",
            })
    
    return history