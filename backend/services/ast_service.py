import re
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

# ── Lightweight extractor registry ──────────────────────────────
# Each entry: (func_regex, class_regex) or None to fallback to tree-sitter.
# Only used by /api/repo/symbols — trace_function_across_commits always uses tree-sitter.
_LIGHTWEIGHT = [
    (".py", re.compile(r'^[ \t]*(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE),
           re.compile(r'^[ \t]*class\s+(\w+)\s*[:\(]', re.MULTILINE)),
    (".go", re.compile(r'^[ \t]*func\s+(\w+)\s*\(', re.MULTILINE),
           re.compile(r'^[ \t]*type\s+(\w+)\s+(struct|interface)\b', re.MULTILINE)),
    (".rs", re.compile(r'^[ \t]*(?:pub\s+(?:unsafe\s+)?)?fn\s+(\w+)\s*[\(<]', re.MULTILINE),
           re.compile(r'^[ \t]*(?:pub\s+)?(?:struct|trait|enum|union)\s+(\w+)', re.MULTILINE)),
    (".java", re.compile(r'(?:\bpublic\b|\bprivate\b|\bprotected\b|\bstatic\b|\bfinal\b|\s)*\s+(\w+)\s*\(', re.MULTILINE),
           re.compile(r'^[ \t]*(?:public\s+|private\s+|protected\s+|static\s+|abstract\s+|final\s+)*\s*(?:class|interface|enum|record)\s+(\w+)', re.MULTILINE)),
    (".kt", re.compile(r'^[ \t]*(?:private\s+|public\s+|internal\s+|protected\s+|suspend\s+|inline\s+|override\s+|fun\s+)*\s*fun\s+(\w+)\s*[\(<]', re.MULTILINE),
           re.compile(r'^[ \t]*(?:private\s+|public\s+|internal\s+|protected\s+|data\s+|sealed\s+|open\s+|abstract\s+)*\s*(?:class|interface|enum|object)\s+(\w+)', re.MULTILINE)),
]
# Map extension → (func_regex, class_regex)
_LIGHTWEIGHT_MAP = {}
for ext, fre, cre in _LIGHTWEIGHT:
    _LIGHTWEIGHT_MAP[ext] = (fre, cre)

# JS/TS extensions — always use tree-sitter
_JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_PY_KEYWORDS = frozenset({
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return",
    "try", "while", "with", "yield",
})


def extract_symbols_fast(source_code: str, file_path: str) -> dict:
    """
    轻量符号提取——仅用于 /api/repo/symbols（前端展示）。
    返回 {functions: [{name, start_line}], classes: [{name, start_line}]}

    有 regex 注册的语言走 regex，JS/TS 走 tree-sitter fallback。
    """
    ext = Path(file_path).suffix.lower()

    # ── JS/TS: use tree-sitter ──
    if ext in _JS_EXTS:
        lang = JS_LANGUAGE
        parser = Parser(lang)
        tree = parser.parse(bytes(source_code, "utf8"))
        functions = []
        classes = []
        def walk(node):
            if node.type in FUNC_TYPES:
                nn = node.child_by_field_name("name")
                if nn:
                    functions.append({
                        "name": source_code[nn.start_byte:nn.end_byte],
                        "start_line": node.start_point[0] + 1,
                    })
            if node.type in CLASS_TYPES:
                nn = node.child_by_field_name("name")
                if nn:
                    classes.append({
                        "name": source_code[nn.start_byte:nn.end_byte],
                        "start_line": node.start_point[0] + 1,
                    })
            for child in node.children:
                walk(child)
        walk(tree.root_node)
        return {"functions": functions, "classes": classes}

    # ── Regex languages ──
    pair = _LIGHTWEIGHT_MAP.get(ext)
    if pair is None:
        # Unknown extension: fallback to Python
        pair = _LIGHTWEIGHT_MAP[".py"]

    func_re, class_re = pair

    functions = []
    for m in func_re.finditer(source_code):
        name = m.group(1)
        if ext == ".py" and name in _PY_KEYWORDS:
            continue
        line = source_code[:m.start()].count("\n") + 1
        functions.append({"name": name, "start_line": line})

    classes = []
    for m in class_re.finditer(source_code):
        name = m.group(1)
        if ext == ".py" and name in _PY_KEYWORDS:
            continue
        line = source_code[:m.start()].count("\n") + 1
        classes.append({"name": name, "start_line": line})

    # Deduplicate by line number
    seen_funcs = set()
    unique_funcs = []
    for f in functions:
        key = (f["name"], f["start_line"])
        if key not in seen_funcs:
            seen_funcs.add(key)
            unique_funcs.append(f)

    seen_classes = set()
    unique_classes = []
    for c in classes:
        key = (c["name"], c["start_line"])
        if key not in seen_classes:
            seen_classes.add(key)
            unique_classes.append(c)

    return {"functions": unique_funcs, "classes": unique_classes}


def get_language_for_file(file_path: str):
    if file_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        return JS_LANGUAGE
    return PY_LANGUAGE


def extract_functions(source_code: str, language=None) -> list[dict]:
    """
    提取源代码中的函数定义。
    对 Python 用 regex（可靠）+ tree-sitter（补 body）；
    对 JS/TS 用 tree-sitter。

    Returns:
        list[dict]: {name, start_line, end_line, body}
    """
    # ── Python: hybrid regex + tree-sitter ──
    if language is None or language is PY_LANGUAGE:
        return _extract_python_functions(source_code)

    # ── JS/TS: tree-sitter only ──
    parser = Parser(language)
    tree = parser.parse(bytes(source_code, "utf8"))
    functions = []

    def walk(node):
        if node.type in FUNC_TYPES:
            name_node = node.child_by_field_name("name")
            body_node = node.child_by_field_name("body")
            if name_node and body_node:
                name = source_code[name_node.start_byte:name_node.end_byte]
                functions.append({
                    "name": name,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "body": source_code[node.start_byte:node.end_byte],
                })
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return functions


_PY_FUNC_RE = re.compile(r'^[ \t]*(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE)
_PY_CLASS_RE = re.compile(r'^[ \t]*class\s+(\w+)\s*[:\(]', re.MULTILINE)


def _extract_python_functions(source_code: str) -> list[dict]:
    """Python 专用：regex 定位函数名 + tree-sitter 补 body。"""
    # Step 1: regex 找到所有 def（可靠，不遗漏）
    matches = list(_PY_FUNC_RE.finditer(source_code))

    # Step 2: tree-sitter 解析 body（能拿多少拿多少）
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(bytes(source_code, "utf8"))
    ts_funcs = {}  # (name, line) → {start_line, end_line, body}

    def walk(node):
        if node.type in FUNC_TYPES:
            nn = node.child_by_field_name("name")
            bn = node.child_by_field_name("body")
            if nn and bn:
                name = source_code[nn.start_byte:nn.end_byte]
                line = node.start_point[0] + 1
                ts_funcs[(name, line)] = {
                    "name": name,
                    "start_line": line,
                    "end_line": node.end_point[0] + 1,
                    "body": source_code[node.start_byte:node.end_byte],
                }
        for child in node.children:
            walk(child)
    walk(tree.root_node)

    # Step 3: 合并——regex 做骨架，tree-sitter 补充 body/end_line
    seen = set()
    functions = []
    for m in matches:
        name = m.group(1)
        line = source_code[:m.start()].count("\n") + 1
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)

        ts = ts_funcs.get(key)
        functions.append({
            "name": name,
            "start_line": line,
            "end_line": ts["end_line"] if ts else line,
            "body": ts["body"] if ts else "",
        })
    return functions


def extract_classes(source_code: str, language=None) -> list[dict]:
    """
    提取源代码中的 class 定义及其方法列表。
    对 Python 用 regex + tree-sitter hybrid；对 JS/TS 用 tree-sitter。

    Returns:
        list[dict]: 每项含 name / start_line / end_line / body / methods
    """
    if language is None or language is PY_LANGUAGE:
        return _extract_python_classes(source_code)

    # ── JS/TS: tree-sitter only ──
    parser = Parser(language)
    tree = parser.parse(bytes(source_code, "utf8"))
    classes = []

    def walk_methods(node):
        methods = []
        if node.type in FUNC_TYPES:
            nn = node.child_by_field_name("name")
            bn = node.child_by_field_name("body")
            if nn and bn:
                methods.append({
                    "name": source_code[nn.start_byte:nn.end_byte],
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "body": source_code[node.start_byte:node.end_byte],
                })
        for child in node.children:
            methods.extend(walk_methods(child))
        return methods

    def walk(node):
        if node.type in CLASS_TYPES:
            nn = node.child_by_field_name("name")
            bn = node.child_by_field_name("body")
            if nn and bn:
                classes.append({
                    "name": source_code[nn.start_byte:nn.end_byte],
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "body": source_code[node.start_byte:node.end_byte],
                    "methods": walk_methods(bn),
                })
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return classes


def _extract_python_classes(source_code: str) -> list[dict]:
    """Python 专用：regex 定位类名 + tree-sitter 补 body/methods。"""
    matches = list(_PY_CLASS_RE.finditer(source_code))

    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(bytes(source_code, "utf8"))
    ts_classes = {}

    def walk_methods(node):
        methods = []
        if node.type in FUNC_TYPES:
            nn = node.child_by_field_name("name")
            bn = node.child_by_field_name("body")
            if nn and bn:
                methods.append({
                    "name": source_code[nn.start_byte:nn.end_byte],
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "body": source_code[node.start_byte:node.end_byte],
                })
        for child in node.children:
            methods.extend(walk_methods(child))
        return methods

    def walk(node):
        if node.type in CLASS_TYPES:
            nn = node.child_by_field_name("name")
            bn = node.child_by_field_name("body")
            if nn and bn:
                name = source_code[nn.start_byte:nn.end_byte]
                line = node.start_point[0] + 1
                ts_classes[(name, line)] = {
                    "name": name,
                    "start_line": line,
                    "end_line": node.end_point[0] + 1,
                    "body": source_code[node.start_byte:node.end_byte],
                    "methods": walk_methods(bn),
                }
        for child in node.children:
            walk(child)
    walk(tree.root_node)

    seen = set()
    classes = []
    for m in matches:
        name = m.group(1)
        line = source_code[:m.start()].count("\n") + 1
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        ts = ts_classes.get(key)
        classes.append({
            "name": name,
            "start_line": line,
            "end_line": ts["end_line"] if ts else line,
            "body": ts["body"] if ts else "",
            "methods": ts["methods"] if ts else [],
        })
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