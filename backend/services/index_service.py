"""持久化预索引服务 — SQLite 事实表 + 派生查询。

设计（2026-07-31 拍板，见 .loci/decisions/2026-07-31-持久化预索引方案.md）：
- 存储：每个仓库一个 SQLite 库文件 `<INDEX_DIR>/<repo>.db`
  （INDEX_DIR = CODETRACE_INDEX_DIR，未设置则用 CODETRACE_CACHE/index）
- 事实表：commits / files / file_commits(核心) / symbols / pr_cache
- 不物化 co_change_pairs：从 file_commits 用 JOIN 派生（实测 4.5ms）
- 建索引：一次 `git log --numstat` 遍历产出 commits + file_commits，
  加一次 ls-tree（文件清单）和一次 AST 符号提取，共 3 次扫描
- 增量更新：head 变化时只读 `git log old_head..HEAD` 的新 commit，
  files/symbols 全量刷新（快）
- 任何失败 → 调用方静默回退原 git 路径，不破坏现有功能
"""

import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from services import metrics

_FRESH_CACHE: dict[Path, tuple[bool, float]] = {}
_FRESH_TTL = 30.0  # 秒，避免每次请求都跑 rev-parse
_PR_CACHE_TTL = 7 * 24 * 3600  # PR 信息缓存 7 天
_SCHEMA_VERSION = "2"  # 索引 schema 版本，升级时强制全量重建

_BUILD_THREADS: dict[str, threading.Thread] = {}
_BUILD_THREADS_LOCK = threading.Lock()
_STATUS_WRITE_LOCK = threading.Lock()


# ── 路径 ──────────────────────────────────────────────


def _cache_root() -> Path:
    return Path(os.getenv("CODETRACE_CACHE", "/tmp/codetrace"))


def _index_dir() -> Path:
    override = os.getenv("CODETRACE_INDEX_DIR")
    return Path(override) if override else _cache_root() / "index"


def index_dir() -> Path:
    """索引根目录（公开访问，供追踪等扩展模块使用）。"""
    return _index_dir()


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _db_path_for_name(name: str) -> Path:
    return _index_dir() / f"{_safe_name(name.rstrip('/').replace('.git', ''))}.db"


def _db_path(repo_path: Path) -> Path:
    return _db_path_for_name(Path(repo_path).name)


def _lock_dir() -> Path:
    return _index_dir() / ".locks"


def _connect(db: Path) -> sqlite3.Connection:
    os.makedirs(db.parent, exist_ok=True)
    con = sqlite3.connect(db, timeout=15)
    con.execute("PRAGMA busy_timeout=10000")
    return con


_SCHEMA = """
CREATE TABLE IF NOT EXISTS commits(
    hash TEXT PRIMARY KEY,
    author TEXT,
    date TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS file_commits(
    file TEXT,
    commit_hash TEXT,
    additions INTEGER,
    deletions INTEGER,
    PRIMARY KEY(file, commit_hash)
);
CREATE INDEX IF NOT EXISTS idx_fc_commit ON file_commits(commit_hash);
CREATE TABLE IF NOT EXISTS symbols(
    file TEXT,
    name TEXT,
    kind TEXT,
    line INTEGER,
    PRIMARY KEY(file, name, kind)
);
CREATE TABLE IF NOT EXISTS renames(
    file TEXT,
    prev_file TEXT,
    commit_hash TEXT,
    PRIMARY KEY(file, commit_hash)
);
CREATE INDEX IF NOT EXISTS idx_renames_prev ON renames(prev_file);
CREATE TABLE IF NOT EXISTS pr_cache(
    repo TEXT,
    pr_number INTEGER,
    payload TEXT,
    fetched_at TEXT,
    PRIMARY KEY(repo, pr_number)
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


# ── 并发锁（mkdir 原子性，与 git_runner 同款） ──────────


def _acquire_lock(name: str, timeout: float = 120) -> bool:
    try:
        os.makedirs(_lock_dir(), exist_ok=True)
    except Exception:
        return False
    lock_path = _lock_dir() / _safe_name(name)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.mkdir(lock_path)
            return True
        except FileExistsError:
            time.sleep(0.2)
    return False


def _release_lock(name: str):
    lock_path = _lock_dir() / _safe_name(name)
    try:
        os.rmdir(lock_path)
    except OSError:
        pass


# ── 后台构建（异步触发 + 进度上报） ─────────────────────


def _status_path(repo_path: Path) -> Path:
    return _index_dir() / "status" / f"{_safe_name(Path(repo_path).name)}.json"


def _update_build_status(repo_path: Path, **fields) -> bool:
    """原子更新构建状态（status/stage/message/...）。失败静默。"""
    try:
        with _STATUS_WRITE_LOCK:
            path = _status_path(repo_path)
            os.makedirs(path.parent, exist_ok=True)
            data = {}
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
            data.update(fields)
            data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        return True
    except Exception:
        return False


def get_index_status(repo_path: Path) -> dict | None:
    """读取构建状态；无状态记录返回 None。"""
    try:
        path = _status_path(repo_path)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


_STAGE_MESSAGES = {
    "queued": "排队等待构建",
    "scan": "扫描 commit 历史",
    "files": "刷新文件清单",
    "symbols": "提取代码符号",
    "done": "索引构建完成",
    "error": "索引构建失败，已回退 git 路径",
}


def _background_build(repo_path: Path):
    name = Path(repo_path).name
    try:
        _update_build_status(repo_path, repo=name, status="running", stage="queued",
                             message=_STAGE_MESSAGES["queued"])

        def report(stage: str, detail: dict):
            _update_build_status(
                repo_path,
                status="running",
                stage=stage,
                message=_STAGE_MESSAGES.get(stage, stage),
                **detail,
            )

        try:
            ok = ensure_indexed(repo_path, report=report)
            stage = "done" if ok else "error"
            _update_build_status(repo_path, status=stage, stage=stage,
                                 message=_STAGE_MESSAGES[stage])
        except Exception:
            _update_build_status(repo_path, status="error", stage="error",
                                 message=_STAGE_MESSAGES["error"])
    finally:
        # 线程结束清理注册表，避免长期运行残留
        with _BUILD_THREADS_LOCK:
            if _BUILD_THREADS.get(name) is threading.current_thread():
                del _BUILD_THREADS[name]


def request_index_build(repo_path: Path) -> bool:
    """后台异步触发索引构建（幂等：同仓库已有构建线程则跳过）。"""
    try:
        repo_path = Path(repo_path)
        name = repo_path.name
        with _BUILD_THREADS_LOCK:
            existing = _BUILD_THREADS.get(name)
            if existing and existing.is_alive():
                return False
            t = threading.Thread(
                target=_background_build, args=(repo_path,),
                name=f"index-{name}", daemon=True,
            )
            _BUILD_THREADS[name] = t
            t.start()
            return True
    except Exception:
        return False


def rename_index_for(repo_path_old: Path, repo_path_new: Path) -> bool:
    """迁移索引库与构建状态文件（旧仓库名 → 新仓库名），尽力而为。"""
    try:
        old_db = _db_path(repo_path_old)
        new_db = _db_path(repo_path_new)
        for suffix in ("", "-wal", "-shm"):
            o = Path(str(old_db) + suffix)
            n = Path(str(new_db) + suffix)
            if o.exists():
                os.replace(o, n)
        o_st = _status_path(repo_path_old)
        n_st = _status_path(repo_path_new)
        if o_st.exists():
            os.replace(o_st, n_st)
        with _BUILD_THREADS_LOCK:
            _BUILD_THREADS.pop(Path(repo_path_old).name, None)
        _FRESH_CACHE.pop(Path(repo_path_old).resolve(), None)
        return True
    except Exception:
        return False


# ── git 基础操作 ───────────────────────────────────────


def _git_head(repo_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        head = result.stdout.strip()
        return head or None
    except Exception:
        return None


def _meta_value(con: sqlite3.Connection, key: str, default=None):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _is_fresh(db: Path, head: str | None) -> bool:
    """库存在、head_hash 与当前 HEAD 一致，且 schema 版本匹配。"""
    if head is None or not db.exists():
        return False
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            head_row = con.execute("SELECT value FROM meta WHERE key = 'head_hash'").fetchone()
            ver_row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        finally:
            con.close()
        return (
            bool(head_row)
            and head_row[0] == head
            and bool(ver_row)
            and ver_row[0] == _SCHEMA_VERSION
        )
    except Exception:
        return False


def _mark_fresh(repo_path: Path, fresh: bool = True):
    _FRESH_CACHE[Path(repo_path)] = (fresh, time.time())


def index_fresh(repo_path: Path) -> bool:
    """索引是否与当前 HEAD 一致（轻量检查，不触发构建）。"""
    try:
        repo_path = Path(repo_path)
        now = time.time()
        cached = _FRESH_CACHE.get(repo_path)
        if cached and now - cached[1] < _FRESH_TTL:
            return cached[0]
        head = _git_head(repo_path)
        fresh = head is not None and _is_fresh(_db_path(repo_path), head)
        _FRESH_CACHE[repo_path] = (fresh, now)
        return fresh
    except Exception:
        return False


# ── 建索引（3 次扫描） ─────────────────────────────────


def _fill_from_numstat(
    repo_path: Path, con: sqlite3.Connection, extra_args: list[str], report=None
):
    """一次 git log --numstat 遍历 → commits + file_commits 事实表。"""
    cmd = ["git", "log", "--numstat", "--pretty=format:__COMMIT__%H|%an|%ai|%s"] + extra_args
    result = subprocess.run(
        cmd, cwd=repo_path, capture_output=True, text=True,
        encoding="utf-8", timeout=600,
    )
    result.check_returncode()

    current = None
    n_commits = 0
    n_rows = 0
    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("__COMMIT__"):
            parts = line[10:].split("|", 3)
            current = (parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else "")
            con.execute("INSERT OR IGNORE INTO commits VALUES (?,?,?,?)", current)
            n_commits += 1
            if report and n_commits % 500 == 0:
                report("scan", {"commits": n_commits, "file_rows": n_rows})
        elif current and "\t" in line:
            try:
                a, d, f = line.split("\t")
            except ValueError:
                continue
            if a == "-" or d == "-":
                continue  # 二进制文件
            try:
                av, dv = int(a or 0), int(d or 0)
            except ValueError:
                continue
            if " => " in f:
                # 重命名行（git 默认开启 rename 检测）：拆成两条事实行
                # （旧路径 0/0，新路径记真实变更行数），同时产出 rename 边供 --follow 等价查询
                old, new = f.split(" => ", 1)
                old = old.strip().strip('"')
                new = new.strip().strip('"')
                con.execute(
                    "INSERT OR IGNORE INTO file_commits VALUES (?,?,?,?)",
                    (old, current[0], 0, 0),
                )
                con.execute(
                    "INSERT OR IGNORE INTO file_commits VALUES (?,?,?,?)",
                    (new, current[0], av, dv),
                )
                con.execute(
                    "INSERT OR IGNORE INTO renames VALUES (?,?,?)",
                    (new, old, current[0]),
                )
                continue
            con.execute(
                "INSERT OR IGNORE INTO file_commits VALUES (?,?,?,?)",
                (f, current[0], av, dv),
            )
            n_rows += 1
    if report:
        report("scan", {"commits": n_commits, "file_rows": n_rows})


def _refresh_files(
    repo_path: Path, con: sqlite3.Connection, report=None
) -> list[str]:
    """ls-tree 刷新文件清单，返回当前全部文件路径。"""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=repo_path, capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )
    result.check_returncode()
    files = [line for line in result.stdout.strip().split("\n") if line]
    con.execute("DELETE FROM files")
    con.executemany("INSERT OR IGNORE INTO files VALUES (?)", [(f,) for f in files])
    if report:
        report("files", {"count": len(files)})
    return files


def _refresh_symbols(
    repo_path: Path, con: sqlite3.Connection, files: list[str], report=None
):
    """AST 符号全量重提（磁盘读取，快）。"""
    # 延迟导入，避免与 git_stats 形成循环依赖
    from services.ast_service import extract_symbols_fast

    con.execute("DELETE FROM symbols")
    rows = []
    for i, fp in enumerate(files):
        try:
            p = Path(repo_path) / fp
            if p.stat().st_size > 2 * 1024 * 1024:
                continue  # 跳过超大文件
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        try:
            syms = extract_symbols_fast(content, fp)
        except Exception:
            continue
        for sym in syms.get("functions", []):
            rows.append((fp, sym["name"], "function", sym["start_line"]))
        for sym in syms.get("classes", []):
            rows.append((fp, sym["name"], "class", sym["start_line"]))
        if report and (i + 1) % 500 == 0:
            report("symbols", {"processed": i + 1, "total": len(files), "found": len(rows)})
    con.executemany("INSERT OR IGNORE INTO symbols VALUES (?,?,?,?)", rows)
    if report:
        report("symbols", {"processed": len(files), "total": len(files), "found": len(rows)})


def _write_meta(con: sqlite3.Connection, head: str):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    con.execute("INSERT OR REPLACE INTO meta VALUES ('head_hash', ?)", (head,))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (_SCHEMA_VERSION,))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('indexed_at', ?)", (now,))
    con.commit()


def _build_full(repo_path: Path, con: sqlite3.Connection, head: str, report=None):
    con.executescript(_SCHEMA)
    for table in ("file_commits", "commits", "files", "symbols", "renames"):
        con.execute(f"DELETE FROM {table}")
    _fill_from_numstat(repo_path, con, ["--reverse"], report=report)
    files = _refresh_files(repo_path, con, report=report)
    _refresh_symbols(repo_path, con, files, report=report)
    _write_meta(con, head)


def _build_incremental(
    repo_path: Path, con: sqlite3.Connection, old_head: str, head: str, report=None
) -> bool:
    """增量更新：只读 old_head..HEAD 的新 commit。历史不连续时返回 False。"""
    try:
        check = subprocess.run(
            ["git", "merge-base", "--is-ancestor", old_head, head],
            cwd=repo_path, capture_output=True, timeout=15,
        )
        if check.returncode != 0:
            return False  # 历史不连续（force push / 浅克隆边界）→ 全量重建
        _fill_from_numstat(repo_path, con, [f"{old_head}..{head}"], report=report)
        files = _refresh_files(repo_path, con, report=report)
        _refresh_symbols(repo_path, con, files, report=report)
        _write_meta(con, head)
        return True
    except Exception:
        return False


def ensure_indexed(repo_path: Path, report=None) -> bool:
    """确保仓库索引存在且与当前 HEAD 一致。任何失败都返回 False（调用方静默回退 git）。"""
    try:
        repo_path = Path(repo_path)
        if not (repo_path / ".git").exists():
            return False
        head = _git_head(repo_path)
        if head is None:
            return False
        db = _db_path(repo_path)
        if _is_fresh(db, head):
            _mark_fresh(repo_path, True)
            return True
        if not _acquire_lock(repo_path.name, timeout=120):
            return False
        try:
            if _is_fresh(db, head):
                _mark_fresh(repo_path, True)
                return True
            con = _connect(db)
            try:
                con.executescript(_SCHEMA)
                old_head = _meta_value(con, "head_hash")
                old_ver = _meta_value(con, "schema_version")
                if old_head and old_ver == _SCHEMA_VERSION and not _build_incremental(
                    repo_path, con, old_head, head, report=report
                ):
                    _build_full(repo_path, con, head, report=report)
                elif not old_head or old_ver != _SCHEMA_VERSION:
                    _build_full(repo_path, con, head, report=report)
            finally:
                con.close()
            _mark_fresh(repo_path, True)
            return True
        finally:
            _release_lock(repo_path.name)
    except Exception:
        return False


# ── 派生查询（热路径） ─────────────────────────────────


def get_file_commits(repo_path: Path, file_path: str) -> list[dict]:
    """文件 commit 历史（等价 git log --follow：沿时间向后跟随重命名）。"""
    con = _connect(_db_path(repo_path))
    try:
        commits = {}

        def _collect(f: str, boundary: str | None = None):
            if boundary is None:
                rows = con.execute(
                    """SELECT c.hash, c.author, c.date, c.message
                       FROM file_commits fc JOIN commits c ON fc.commit_hash = c.hash
                       WHERE fc.file = ?""",
                    (f,),
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT c.hash, c.author, c.date, c.message
                       FROM file_commits fc JOIN commits c ON fc.commit_hash = c.hash
                       WHERE fc.file = ? AND c.date < ?""",
                    (f, boundary),
                ).fetchall()
            for r in rows:
                commits.setdefault(r[0], {"hash": r[0], "author": r[1], "date": r[2], "message": r[3]})

        _collect(file_path)
        seen_paths = {file_path}
        queue = [file_path]
        while queue:
            f = queue.pop()
            # 向后跟随：file 由 prev_file 改名而来 → 取改名 commit 之前的 prev_file 历史
            for ch, prev in con.execute(
                "SELECT commit_hash, prev_file FROM renames WHERE file = ?", (f,)
            ).fetchall():
                if prev in seen_paths:
                    continue
                seen_paths.add(prev)
                boundary = con.execute(
                    "SELECT date FROM commits WHERE hash = ?", (ch,)
                ).fetchone()
                if boundary:
                    _collect(prev, boundary[0])
                queue.append(prev)
    finally:
        con.close()
    return sorted(commits.values(), key=lambda c: (c["date"], c["hash"]), reverse=True)


def get_commit_diff_stats(repo_path: Path, commit_hash: str) -> dict:
    con = _connect(_db_path(repo_path))
    try:
        row = con.execute(
            """SELECT COUNT(*), COALESCE(SUM(additions), 0), COALESCE(SUM(deletions), 0)
               FROM file_commits WHERE commit_hash = ?""",
            (commit_hash,),
        ).fetchone()
        return {
            "additions": row[1],
            "deletions": row[2],
            "files_changed": row[0],
        }
    finally:
        con.close()


def list_files_at_head(repo_path: Path) -> list[str]:
    con = _connect(_db_path(repo_path))
    try:
        rows = con.execute("SELECT path FROM files ORDER BY path").fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def get_top_changed_files(repo_path: Path, top_n: int = 10) -> list[str]:
    con = _connect(_db_path(repo_path))
    try:
        rows = con.execute(
            """SELECT file FROM file_commits
               GROUP BY file ORDER BY COUNT(*) DESC, file LIMIT ?""",
            (top_n,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def get_file_commit_counts(repo_path: Path) -> dict:
    con = _connect(_db_path(repo_path))
    try:
        rows = con.execute(
            "SELECT file, COUNT(*) FROM file_commits GROUP BY file"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        con.close()


def get_repo_summary(repo_path: Path) -> dict:
    con = _connect(_db_path(repo_path))
    try:
        total_commits = con.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
        total_files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        total_authors = con.execute(
            "SELECT COUNT(DISTINCT author) FROM commits"
        ).fetchone()[0]
        recent = con.execute(
            """SELECT author, date, message FROM commits
               ORDER BY date DESC, hash DESC LIMIT 15"""
        ).fetchall()
        recent_commits = [
            {"author": r[0], "date": r[1][:10], "message": r[2][:80]}
            for r in recent
        ]
    finally:
        con.close()
    return {
        "total_commits": total_commits,
        "total_files": total_files,
        "total_authors": total_authors,
        "top_files": get_top_changed_files(repo_path, top_n=10),
        "recent_commits": recent_commits,
    }


def get_file_health_stats(repo_path: Path, top_n: int = 20) -> list[dict]:
    con = _connect(_db_path(repo_path))
    try:
        rows = con.execute(
            """SELECT fc.file,
                      COUNT(*),
                      COALESCE(SUM(fc.additions), 0),
                      COALESCE(SUM(fc.deletions), 0),
                      GROUP_CONCAT(DISTINCT c.author)
               FROM file_commits fc JOIN commits c ON fc.commit_hash = c.hash
               GROUP BY fc.file"""
        ).fetchall()
        recency_rows = con.execute(
            f"""SELECT fc.file, {metrics._recency_bucket_sql()}
                FROM file_commits fc JOIN commits c ON fc.commit_hash = c.hash
                GROUP BY fc.file"""
        ).fetchall()
    finally:
        con.close()
    recency = {r[0]: r[1] for r in recency_rows}

    def _messages_batch(files):
        con = _connect(_db_path(repo_path))
        try:
            if not files:
                return {}
            out = {}
            # 分批查询，避免单条 IN 超过 SQLite 变量上限
            for i in range(0, len(files), 100):
                chunk = files[i:i + 100]
                placeholders = ",".join("?" * len(chunk))
                msgs = con.execute(
                    f"""SELECT file, message FROM (
                           SELECT fc.file AS file, c.message AS message,
                                  ROW_NUMBER() OVER (
                                    PARTITION BY fc.file ORDER BY c.date ASC
                                  ) AS rn
                           FROM file_commits fc
                           JOIN commits c ON fc.commit_hash = c.hash
                           WHERE fc.file IN ({placeholders})
                         ) WHERE rn <= 5 ORDER BY rn""",
                    chunk,
                ).fetchall()
                for f, m in msgs:
                    out.setdefault(f, []).append(m)
            return out
        finally:
            con.close()

    stat_rows = [
        {
            "file": f,
            "total_commits": total,
            "total_additions": add,
            "total_deletions": dele,
            "authors": authors,
        }
        for f, total, add, dele, authors in rows
    ]
    return metrics.assemble_health_stats(
        stat_rows, recency, top_n=top_n, messages_of=_messages_batch,
    )


def get_recent_commit_groups(repo_path: Path, count: int = 15) -> list[dict]:
    con = _connect(_db_path(repo_path))
    try:
        commits = con.execute(
            """SELECT hash, author, date, message FROM commits
               ORDER BY date DESC, hash DESC LIMIT ?""",
            (count,),
        ).fetchall()
        if not commits:
            return []
        placeholders = ",".join("?" * len(commits))
        fc_rows = con.execute(
            f"""SELECT commit_hash, file, additions, deletions FROM file_commits
                WHERE commit_hash IN ({placeholders})""",
            [c[0] for c in commits],
        ).fetchall()
    finally:
        con.close()

    by_commit = {}
    for ch, f, a, d in fc_rows:
        by_commit.setdefault(ch, []).append({"path": f, "additions": a, "deletions": d})

    groups = []
    for ch, author, date, message in commits:
        files = by_commit.get(ch, [])
        if not files:
            continue
        groups.append({
            "commit_hash": ch,
            "author": author,
            "date": date,
            "message": message,
            "files": files,
            "file_count": len(files),
            "total_churn": sum(f["additions"] + f["deletions"] for f in files),
        })
    return groups


# ── co-change（从 file_commits 派生，不物化） ───────────


def _window_groups(con: sqlite3.Connection, since: str, until: str) -> list[set[str]]:
    """按 commit 分组取某时间窗内的文件集（对齐 git log --since/--until 语义）。"""
    rows = con.execute(
        """SELECT fc.commit_hash, fc.file FROM file_commits fc
           JOIN commits c ON fc.commit_hash = c.hash
           WHERE c.date >= ? AND c.date < ?
           ORDER BY fc.commit_hash""",
        (since, until),
    ).fetchall()
    groups = {}
    for ch, f in rows:
        groups.setdefault(ch, set()).add(f)
    return list(groups.values())


def get_co_change_trends(repo_path: Path, window_days: int = 30) -> list[dict]:
    import datetime

    now = datetime.datetime.now()
    since_r = (now - datetime.timedelta(days=window_days)).strftime("%Y-%m-%d")
    until_r = now.strftime("%Y-%m-%d")
    since_o = (now - datetime.timedelta(days=window_days * 2)).strftime("%Y-%m-%d")
    until_o = since_r

    con = _connect(_db_path(repo_path))
    try:
        recent_groups = _window_groups(con, since_r, until_r)
        old_groups = _window_groups(con, since_o, until_o)
    finally:
        con.close()

    return metrics.compute_cochange_trends(recent_groups, old_groups)


def get_co_change_edges(repo_path: Path, window_days: int = 30) -> dict:
    import datetime

    now = datetime.datetime.now()
    since_r = (now - datetime.timedelta(days=window_days)).strftime("%Y-%m-%d")
    until_r = now.strftime("%Y-%m-%d")
    since_o = (now - datetime.timedelta(days=window_days * 2)).strftime("%Y-%m-%d")
    until_o = since_r

    con = _connect(_db_path(repo_path))
    try:
        recent_groups = _window_groups(con, since_r, until_r)
        old_groups = _window_groups(con, since_o, until_o)
    finally:
        con.close()

    return metrics.compute_cochange_edges(recent_groups, old_groups)


# ── 符号查询（/api/repo/symbols 的 HEAD 快路径） ────────


def get_symbols(repo_path: Path, file_path: str) -> dict | None:
    """返回指定文件在 HEAD 的函数/类符号。文件不在索引中时返回 None。"""
    db = _db_path(repo_path)
    if not db.exists():
        return None
    con = _connect(db)
    try:
        exists = con.execute("SELECT 1 FROM files WHERE path = ?", (file_path,)).fetchone()
        if not exists:
            return None
        rows = con.execute(
            "SELECT name, kind, line FROM symbols WHERE file = ? ORDER BY line",
            (file_path,),
        ).fetchall()
    finally:
        con.close()
    return {
        "functions": [{"name": r[0], "start_line": r[2]} for r in rows if r[1] == "function"],
        "classes": [{"name": r[0], "start_line": r[2]} for r in rows if r[1] == "class"],
    }


# ── PR 信息缓存 ────────────────────────────────────────


def get_cached_pr(repo_full: str, pr_number: int) -> dict | None:
    """读取缓存的 PR 信息；不存在或过期返回 None。"""
    db = _db_path_for_name(repo_full.split("/")[-1])
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            row = con.execute(
                "SELECT payload, fetched_at FROM pr_cache WHERE repo = ? AND pr_number = ?",
                (repo_full, pr_number),
            ).fetchone()
        finally:
            con.close()
        if not row:
            return None
        fetched = row[1] or ""
        if fetched:
            try:
                fetched_ts = datetime.fromisoformat(fetched).timestamp()
                if time.time() - fetched_ts > _PR_CACHE_TTL:
                    return None
            except Exception:
                pass
        return json.loads(row[0])
    except Exception:
        return None


def set_cached_pr(repo_full: str, pr_number: int, payload: dict) -> bool:
    """写入 PR 信息缓存。失败静默返回 False。"""
    try:
        db = _db_path_for_name(repo_full.split("/")[-1])
        os.makedirs(db.parent, exist_ok=True)
        con = sqlite3.connect(db, timeout=15)
        try:
            con.execute("PRAGMA busy_timeout=10000")
            con.execute(
                """CREATE TABLE IF NOT EXISTS pr_cache(
                    repo TEXT, pr_number INTEGER, payload TEXT, fetched_at TEXT,
                    PRIMARY KEY(repo, pr_number))"""
            )
            con.execute(
                "INSERT OR REPLACE INTO pr_cache VALUES (?,?,?,?)",
                (
                    repo_full,
                    pr_number,
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )
            con.commit()
        finally:
            con.close()
        return True
    except Exception:
        return False
