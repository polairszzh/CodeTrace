import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
from tree_sitter import Language, Parser
from pathlib import Path
from services.git_service import get_file_commits, get_file_content_at_commit, list_files_changed_in_commit, list_files_at_commit
from services.llm_service import LLMService

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())

FUNC_TYPES = {"function_definition", "function_declaration", "arrow_function", "method_definition"}
CLASS_TYPES = {"class_definition", "class_declaration"}

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


def extract_classes(source_code: str, language=None) -> list[dict]:
    """
    提取源代码中的 class 定义及其方法列表。

    Args:
        source_code (str): 源代码字符串。
        language: tree-sitter Language 对象。

    Returns:
        list[dict]: 每项含 name / start_line / end_line / body / methods
    """
    if language is None:
        language = PY_LANGUAGE
    parser = Parser(language)

    tree = parser.parse(bytes(source_code, "utf8"))
    classes = []

    def walk_methods(node):
        """在 class body 内收集方法定义"""
        methods = []
        if node.type in FUNC_TYPES:
            name_node = node.child_by_field_name("name")
            body_node = node.child_by_field_name("body")
            if name_node and body_node:
                methods.append({
                    "name": source_code[name_node.start_byte:name_node.end_byte],
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "body": source_code[node.start_byte:node.end_byte],
                })
        for child in node.children:
            methods.extend(walk_methods(child))
        return methods

    def walk(node):
        if node.type in CLASS_TYPES:
            name_node = node.child_by_field_name("name")
            body_node = node.child_by_field_name("body")
            if name_node and body_node:
                methods = walk_methods(body_node)
                classes.append({
                    "name": source_code[name_node.start_byte:name_node.end_byte],
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "body": source_code[node.start_byte:node.end_byte],
                    "methods": methods,
                })
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return classes


def search_function_across_files(
    repo_path: Path,
    commit_hash: str,
    function_name: str,
    old_body: str = "",
    exclude_file: str = "",
) -> list[dict]:
    """
    在指定 commit 的所有文件中搜索同名/同语义函数。
    优先查本次 commit 变更过的文件（更可能是迁移目标），再查全量文件。
    精确名称匹配不到时，用 LLM 做语义匹配（改名后跨文件迁移）。

    Args:
        repo_path: 仓库本地路径。
        commit_hash: commit 哈希。
        function_name: 要搜索的函数名。
        old_body: 原函数体，用于 LLM 语义匹配兜底。
        exclude_file: 排除的原始文件名（函数消失的那个文件）。

    Returns:
        list[dict]: 找到的匹配，每项含 {file, name, start_line, end_line, body}
    """
    results = []

    # 缩小搜索范围：优先在本次 commit 变更过的文件中找
    changed_files = list_files_changed_in_commit(repo_path, commit_hash)
    candidates = changed_files
    if not candidates:
        candidates = list_files_at_commit(repo_path, commit_hash)

    # 第一阶段：精确名称匹配
    all_functions_found = []
    for fp in candidates:
        fp = fp.strip()
        if not fp or fp == exclude_file:
            continue
        try:
            content = get_file_content_at_commit(repo_path, commit_hash, fp)
        except Exception:
            continue

        lang = get_language_for_file(fp)
        try:
            functions = extract_functions(content, lang)
        except Exception:
            continue

        for fn in functions:
            all_functions_found.append({"file": fp, **fn})
            if fn["name"] == function_name:
                results.append({"file": fp, **fn})

    if results:
        return results

    # 第二阶段：同名找不到且提供了 old_body → LLM 语义匹配
    if not old_body or not all_functions_found:
        return []

    from services.llm_service import LLMService
    llm = LLMService()
    match = llm.match_function_across_files(
        function_name, old_body,
        [{"file": f["file"], "name": f["name"], "body": f.get("body", "")}
         for f in all_functions_found],
    )
    if match and match.get("matched"):
        file_match = match.get("file", "")
        name_match = match.get("name", "")
        for fn in all_functions_found:
            if fn["file"] == file_match and fn["name"] == name_match:
                fn["llm_note"] = match.get("note", "LLM 语义匹配")
                return [fn]

    return []

def trace_function_across_commits(repo_path: Path, file_path: str, function_name: str) -> dict:
    """
    追踪一个函数在文件变更历史中的演变，支持跨文件迁移追踪。

    遍历文件的所有 commit，在每个版本中用 AST 解析函数定义。
    如果函数在某次 commit 后从当前文件消失，自动搜索其他文件，
    找到后继则记录迁移路径并接续追溯。

    Returns:
        dict 包含:
        - history: list[dict] 按时间倒序的提交记录
        - migration_path: list[dict] 迁移事件列表，每项含
          {from_file, to_file, from_func, to_func, commit_hash, note}
    """
    commits = get_file_commits(repo_path, file_path)
    history = []
    migration_path = []
    last_known_body = None
    llm = LLMService()

    current_file = file_path
    current_func = function_name
    current_lang = get_language_for_file(current_file)

    for c in commits:
        try:
            content = get_file_content_at_commit(repo_path, c["hash"], current_file)
        except Exception:
            continue

        functions = extract_functions(content, current_lang)
        matched = [f for f in functions if f["name"] == current_func]

        if matched:
            history.append({
                "commit_hash": c["hash"],
                "author": c["author"],
                "date": c["date"],
                "message": c["message"],
                "function": matched[0],
                "file": current_file,
            })
            last_known_body = matched[0]["body"]
            continue

        # 函数在当前文件消失了 → 先查同名文件内改名，再搜跨文件
        found_in_migration = False

        # 阶段1：同文件 LLM 改名匹配（现有逻辑）
        if last_known_body and functions:
            new_names = [f["name"] for f in functions]
            result = llm.trace_function_change(current_func, last_known_body, new_names)
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
                        "file": current_file,
                        "llm_note": result.get("note", f"疑似重命名为 {name_found}"),
                    })
                    last_known_body = new_matched[0]["body"]
                    current_func = name_found
                    found_in_migration = True

        # 阶段2：跨文件搜索（同文件改名没找到时触发）
        if not found_in_migration and last_known_body:
            cross_results = search_function_across_files(
                repo_path, c["hash"], current_func,
                old_body=last_known_body,
                exclude_file=current_file,
            )
            if cross_results:
                target = cross_results[0]
                target_file = target["file"]
                target_name = target["name"]
                note = target.get("llm_note", f"迁移至 {target_file}")

                history.append({
                    "commit_hash": c["hash"],
                    "author": c["author"],
                    "date": c["date"],
                    "message": c["message"],
                    "function": {"name": target_name, "start_line": target["start_line"],
                                 "end_line": target["end_line"], "body": target.get("body", "")},
                    "file": target_file,
                    "llm_note": note,
                    "migration": True,
                })
                migration_path.append({
                    "from_file": current_file,
                    "to_file": target_file,
                    "from_func": current_func,
                    "to_func": target_name,
                    "commit_hash": c["hash"],
                    "note": note,
                })
                # 切到新文件，接续追溯后续 commit
                current_file = target_file
                current_func = target_name
                current_lang = get_language_for_file(current_file)
                last_known_body = target.get("body", "")
                found_in_migration = True

        if not found_in_migration:
            # 完全消失了（只在至少见过一次函数后才记录消失）
            if last_known_body:
                note = ""
                if functions:
                    r = llm.trace_function_change(current_func, last_known_body, [f["name"] for f in functions])
                    if r:
                        note = r.get("note", "")
                history.append({
                    "commit_hash": c["hash"],
                    "author": c["author"],
                    "date": c["date"],
                    "message": c["message"],
                    "function": None,
                    "file": current_file,
                    "llm_note": note or "函数已消失",
                })

    return {
        "history": history,
        "migration_path": migration_path,
    }


def trace_class_across_commits(repo_path: Path, file_path: str, class_name: str) -> dict:
    """
    追踪一个 class 在文件变更历史中的演变，支持跨文件迁移。

    遍历文件的所有 commit，在每个版本中用 AST 解析 class 定义。
    追踪 methods 增减、class 重命名和跨文件迁移。

    Returns:
        dict 包含:
        - history: list[dict] 按时间顺序的提交记录
        - migration_path: list[dict] 迁移事件列表
    """
    commits = get_file_commits(repo_path, file_path)
    history = []
    migration_path = []
    last_method_names = set()
    last_known_body = None
    llm = LLMService()

    current_file = file_path
    current_class = class_name
    current_lang = get_language_for_file(current_file)

    for c in commits:
        try:
            content = get_file_content_at_commit(repo_path, c["hash"], current_file)
        except Exception:
            continue

        classes = extract_classes(content, current_lang)
        matched = [k for k in classes if k["name"] == current_class]

        if matched:
            klass = matched[0]
            current_methods = {m["name"] for m in klass["methods"]}
            added = list(current_methods - last_method_names)
            removed = list(last_method_names - current_methods)

            history.append({
                "commit_hash": c["hash"],
                "author": c["author"],
                "date": c["date"],
                "message": c["message"],
                "klass": klass,
                "file": current_file,
                "methods_added": added,
                "methods_removed": removed,
            })
            last_known_body = klass["body"]
            last_method_names = current_methods
            continue

        # class 在当前文件消失了 → LLM 改名匹配 + 跨文件搜索
        found_in_migration = False

        # 阶段1：同文件 LLM 改名匹配
        if last_known_body and classes:
            class_names_in_file = [k["name"] for k in classes]
            result = llm.trace_function_change(current_class, last_known_body, class_names_in_file)
            if result and result.get("action") in ("renamed", "split", "merged"):
                name_found = result.get("new_name", "")
                new_matched = [k for k in classes if k["name"] == name_found]
                if new_matched:
                    new_klass = new_matched[0]
                    current_methods = {m["name"] for m in new_klass["methods"]}
                    added = list(current_methods - last_method_names)
                    removed = list(last_method_names - current_methods)
                    history.append({
                        "commit_hash": c["hash"],
                        "author": c["author"],
                        "date": c["date"],
                        "message": c["message"],
                        "klass": new_klass,
                        "file": current_file,
                        "methods_added": added,
                        "methods_removed": removed,
                        "llm_note": result.get("note", f"疑似 class 重命名为 {name_found}"),
                    })
                    last_known_body = new_klass["body"]
                    last_method_names = current_methods
                    current_class = name_found
                    found_in_migration = True

        # 阶段2：跨文件搜索
        if not found_in_migration and last_known_body:
            cross_results = search_function_across_files(
                repo_path, c["hash"], current_class,
                old_body=last_known_body,
                exclude_file=current_file,
            )
            if cross_results:
                target = cross_results[0]
                target_file = target["file"]
                target_name = target["name"]
                note = target.get("llm_note", f"class 迁移至 {target_file}")
                history.append({
                    "commit_hash": c["hash"],
                    "author": c["author"],
                    "date": c["date"],
                    "message": c["message"],
                    "klass": {"name": target_name, "start_line": target["start_line"],
                              "end_line": target["end_line"], "body": target.get("body", ""),
                              "methods": []},
                    "file": target_file,
                    "methods_added": [],
                    "methods_removed": [],
                    "llm_note": note,
                    "migration": True,
                })
                migration_path.append({
                    "from_file": current_file,
                    "to_file": target_file,
                    "from_class": current_class,
                    "to_class": target_name,
                    "commit_hash": c["hash"],
                    "note": note,
                })
                current_file = target_file
                current_class = target_name
                current_lang = get_language_for_file(current_file)
                last_known_body = target.get("body", "")
                last_method_names = set()
                found_in_migration = True

        if not found_in_migration:
            if last_known_body:
                note = ""
                if classes:
                    r = llm.trace_function_change(current_class, last_known_body,
                                                  [k["name"] for k in classes])
                    if r:
                        note = r.get("note", "")
                history.append({
                    "commit_hash": c["hash"],
                    "author": c["author"],
                    "date": c["date"],
                    "message": c["message"],
                    "klass": None,
                    "file": current_file,
                    "methods_added": [],
                    "methods_removed": [],
                    "llm_note": note or "class 已消失",
                })

    return {
        "history": history,
        "migration_path": migration_path,
    }