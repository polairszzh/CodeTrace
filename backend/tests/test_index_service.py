"""持久化预索引测试 — 本地小仓库：全量建索引、增量更新、过期回退、索引与 git 结果一致。"""

import datetime
import os
import subprocess
import time
from pathlib import Path

import pytest

from services import git_service, index_service


def _date(days_ago: int) -> str:
    d = datetime.datetime.now() - datetime.timedelta(days=days_ago)
    return d.strftime("%Y-%m-%dT12:00:00+0800")


def _commit(repo, message: str, date_str: str):
    env = {**os.environ, "GIT_AUTHOR_DATE": date_str, "GIT_COMMITTER_DATE": date_str}
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo, check=True, capture_output=True, env=env,
    )


@pytest.fixture()
def local_repo(tmp_path, monkeypatch):
    """2 个 commit 的本地仓库：旧窗口 1 次（a+b 共变）、近窗口 1 次（a+c 共变）。"""
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    index_service._FRESH_CACHE.clear()

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True, capture_output=True)

    (repo / "a.py").write_text("def alpha():\n    return 1\n\nclass Beta:\n    pass\n", encoding="utf-8")
    (repo / "b.py").write_text("def gamma():\n    return 2\n", encoding="utf-8")
    _commit(repo, "first commit", _date(40))

    (repo / "a.py").write_text(
        "def alpha():\n    return 3\n\ndef delta():\n    return 4\n\nclass Beta:\n    pass\n",
        encoding="utf-8",
    )
    (repo / "c.py").write_text("def epsilon():\n    return 5\n", encoding="utf-8")
    _commit(repo, "second commit (#2)", _date(10))
    return repo


def test_ensure_indexed_builds_full_index(local_repo):
    assert index_service.ensure_indexed(local_repo) is True
    assert index_service.index_fresh(local_repo) is True

    db = index_service._db_path(local_repo)
    assert db.exists()
    con = index_service._connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM file_commits").fetchone()[0] == 4
        # a.py 两次提交各 1 行，b.py/c.py 各 1 行
    finally:
        con.close()

    symbols = index_service.get_symbols(local_repo, "a.py")
    names = {f["name"] for f in symbols["functions"]}
    assert names == {"alpha", "delta"}
    assert [c["name"] for c in symbols["classes"]] == ["Beta"]


def test_hot_path_uses_index_when_fresh(local_repo, monkeypatch):
    assert index_service.ensure_indexed(local_repo)
    index_service._FRESH_CACHE.clear()

    called = []
    original = index_service.get_file_commits

    def spy(repo, file_path):
        called.append(file_path)
        return original(repo, file_path)

    monkeypatch.setattr(index_service, "get_file_commits", spy)
    commits = git_service.get_file_commits(local_repo, "a.py")
    assert called == ["a.py"]
    assert len(commits) == 2


def test_queries_match_git(local_repo, monkeypatch):
    assert index_service.ensure_indexed(local_repo)
    index_service._FRESH_CACHE.clear()

    # 让 git_service 走 git 路径，与索引路径结果逐一对比（真正的 parity 测试）
    monkeypatch.setattr(index_service, "index_fresh", lambda p: False)
    git = git_service.get_file_commits(local_repo, "a.py")
    monkeypatch.setattr(index_service, "index_fresh", lambda p: True)
    idx = index_service.get_file_commits(local_repo, "a.py")
    assert [c["hash"] for c in idx] == [c["hash"] for c in git]
    assert {tuple(sorted(c.items())) for c in idx} == {tuple(sorted(c.items())) for c in git}

    # diff 统计（原 git 版 files_changed 含摘要行，会 +1，这里只对齐行列数）
    head = git[0]["hash"]
    monkeypatch.setattr(index_service, "index_fresh", lambda p: False)
    sgit = git_service.get_commit_diff_stats(local_repo, head)
    monkeypatch.setattr(index_service, "index_fresh", lambda p: True)
    sidx = index_service.get_commit_diff_stats(local_repo, head)
    assert sidx["additions"] == sgit["additions"]
    assert sidx["deletions"] == sgit["deletions"]
    assert sidx["files_changed"] >= 1

    # 热门文件 / 提交计数
    monkeypatch.setattr(index_service, "index_fresh", lambda p: False)
    assert set(index_service.get_top_changed_files(local_repo, 10)) == set(
        git_service.get_top_changed_files(local_repo, 10)
    )
    assert index_service.get_file_commit_counts(local_repo) == git_service.get_file_commit_counts(local_repo)

    # 仓库概要
    s_idx = index_service.get_repo_summary(local_repo)
    s_git = git_service.get_repo_summary(local_repo)
    assert s_idx["total_commits"] == s_git["total_commits"] == 2
    assert s_idx["total_files"] == s_git["total_files"] == 3
    assert s_idx["total_authors"] == s_git["total_authors"]
    assert set(s_idx["top_files"]) == set(s_git["top_files"])
    assert s_idx["recent_commits"] == s_git["recent_commits"]

    # 健康度
    monkeypatch.setattr(index_service, "index_fresh", lambda p: False)
    h_git = {h["file"]: h for h in git_service.get_file_health_stats(local_repo, 10)}
    monkeypatch.setattr(index_service, "index_fresh", lambda p: True)
    h_idx = {h["file"]: h for h in index_service.get_file_health_stats(local_repo, 10)}
    assert set(h_idx) == set(h_git)
    for f in h_idx:
        assert h_idx[f]["total_commits"] == h_git[f]["total_commits"]
        assert h_idx[f]["total_additions"] == h_git[f]["total_additions"]
        assert h_idx[f]["total_deletions"] == h_git[f]["total_deletions"]
        assert h_idx[f]["churn"] == h_git[f]["churn"]
        assert set(h_idx[f]["top_authors"]) == set(h_git[f]["top_authors"])
        assert h_idx[f]["recency_score"] == pytest.approx(h_git[f]["recency_score"], abs=0.1)
        assert h_idx[f]["commit_messages"] == h_git[f]["commit_messages"]

    # 最近 commit 分组
    monkeypatch.setattr(index_service, "index_fresh", lambda p: False)
    g_git = git_service.get_recent_commit_groups(local_repo, 5)
    monkeypatch.setattr(index_service, "index_fresh", lambda p: True)
    g_idx = index_service.get_recent_commit_groups(local_repo, 5)
    assert [g["commit_hash"] for g in g_idx] == [g["commit_hash"] for g in g_git]
    for a, b in zip(g_idx, g_git):
        assert a["file_count"] == b["file_count"]
        assert a["total_churn"] == b["total_churn"]
        assert {(f["path"], f["additions"], f["deletions"]) for f in a["files"]} == {
            (f["path"], f["additions"], f["deletions"]) for f in b["files"]
        }

    # co-change 趋势 / 边
    monkeypatch.setattr(index_service, "index_fresh", lambda p: False)
    t_git = git_service.get_co_change_trends(local_repo, 30)
    monkeypatch.setattr(index_service, "index_fresh", lambda p: True)
    t_idx = index_service.get_co_change_trends(local_repo, 30)
    assert {(t["file"], t["recent_partners"], t["old_partners"]) for t in t_idx} == {
        (t["file"], t["recent_partners"], t["old_partners"]) for t in t_git
    }
    monkeypatch.setattr(index_service, "index_fresh", lambda p: False)
    e_git = git_service.get_co_change_edges(local_repo, 30)
    monkeypatch.setattr(index_service, "index_fresh", lambda p: True)
    e_idx = index_service.get_co_change_edges(local_repo, 30)
    assert {n["id"] for n in e_idx["nodes"]} == {n["id"] for n in e_git["nodes"]}
    assert {tuple(sorted((e["source"], e["target"]))) for e in e_idx["edges"]} == {
        tuple(sorted((e["source"], e["target"]))) for e in e_git["edges"]
    }


def test_incremental_update(local_repo):
    assert index_service.ensure_indexed(local_repo)
    db = index_service._db_path(local_repo)
    con = index_service._connect(db)
    assert con.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 2
    con.close()

    # 新增第 3 个 commit：改 b.py 加新函数
    (local_repo / "b.py").write_text(
        "def gamma():\n    return 2\n\ndef zeta():\n    return 6\n", encoding="utf-8"
    )
    _commit(local_repo, "third commit", _date(2))

    assert index_service.ensure_indexed(local_repo) is True
    assert index_service.index_fresh(local_repo) is True

    con = index_service._connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 3
        rows = con.execute(
            "SELECT COUNT(*) FROM file_commits WHERE file = 'b.py'"
        ).fetchone()[0]
        assert rows == 2
    finally:
        con.close()
    symbols = index_service.get_symbols(local_repo, "b.py")
    assert {f["name"] for f in symbols["functions"]} == {"gamma", "zeta"}


def test_rename_follow_matches_git(tmp_path, monkeypatch):
    """重命名场景：索引 get_file_commits 与 git log --follow 谱系一致（双向跟随）。"""
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    index_service._FRESH_CACHE.clear()

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True, capture_output=True)

    (repo / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    _commit(repo, "c1", _date(40))
    (repo / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    _commit(repo, "c2", _date(30))
    subprocess.run(["git", "mv", "a.py", "d.py"], cwd=repo, check=True, capture_output=True)
    _commit(repo, "c3 rename", _date(20))
    (repo / "d.py").write_text(
        "def alpha():\n    return 1\n\ndef delta():\n    return 3\n", encoding="utf-8"
    )
    _commit(repo, "c4", _date(10))

    assert index_service.ensure_indexed(repo)
    index_service._FRESH_CACHE.clear()

    monkeypatch.setattr(index_service, "index_fresh", lambda p: False)
    git_d = git_service.get_file_commits(repo, "d.py")
    git_a = git_service.get_file_commits(repo, "a.py")
    monkeypatch.setattr(index_service, "index_fresh", lambda p: True)

    idx_d = index_service.get_file_commits(repo, "d.py")
    idx_a = index_service.get_file_commits(repo, "a.py")

    # d.py（新名）：c3(改名) + c1(旧名创建) + c4(改名后修改)；--follow 不向前追，a.py 只有 c1/c3
    assert len(git_d) == 3 and len(git_a) == 2
    assert [c["hash"] for c in idx_d] == [c["hash"] for c in git_d]
    assert [c["hash"] for c in idx_a] == [c["hash"] for c in git_a]
    assert {tuple(sorted(c.items())) for c in idx_d} == {
        tuple(sorted(c.items())) for c in git_d
    }


def test_stale_index_falls_back_to_git(local_repo):
    assert index_service.ensure_indexed(local_repo)
    index_service._FRESH_CACHE.clear()

    # 删除索引库 → index_fresh False，git 路径仍可用
    db = index_service._db_path(local_repo)
    db.unlink()
    assert index_service.index_fresh(local_repo) is False
    assert len(git_service.get_file_commits(local_repo, "a.py")) == 2
    assert git_service.get_file_commit_counts(local_repo) == {"a.py": 2, "b.py": 1, "c.py": 1}

    # 库损坏 → 同样静默回退
    db.write_bytes(b"not a sqlite database")
    index_service._FRESH_CACHE.clear()
    assert index_service.index_fresh(local_repo) is False
    summary = git_service.get_repo_summary(local_repo)
    assert summary["total_commits"] == 2


def test_pr_cache_roundtrip(local_repo):
    assert index_service.set_cached_pr("owner/repo", 42, {"title": "t", "state": "open"}) is True
    cached = index_service.get_cached_pr("owner/repo", 42)
    assert cached == {"title": "t", "state": "open"}
    assert index_service.get_cached_pr("owner/repo", 99) is None


def test_request_index_build_background(local_repo):
    """后台异步建索引：触发后不阻塞，最终完成并写入状态。"""
    index_service._FRESH_CACHE.clear()
    assert index_service.request_index_build(local_repo) is True

    deadline = time.time() + 20
    status = None
    while time.time() < deadline:
        status = index_service.get_index_status(local_repo)
        if status and status.get("status") in ("done", "error"):
            break
        time.sleep(0.2)
    assert status is not None
    assert status.get("status") == "done", f"构建未完成: {status}"

    index_service._FRESH_CACHE.clear()
    assert index_service.index_fresh(local_repo) is True


def test_should_full_clone_decision(monkeypatch):
    """全量 clone 决策：体积 ≤ 1GB 全量，> 阈值或查不到走浅克隆，阈值可配。"""
    monkeypatch.setenv("GITHUB_TOKEN", "")
    git_service._SIZE_CACHE.clear()

    class FakeResp:
        def __init__(self, size_kb):
            self._size = size_kb

        def raise_for_status(self):
            pass

        def json(self):
            return {"size": self._size}

    monkeypatch.setattr(git_service.httpx, "get", lambda url, **kw: FakeResp(100 * 1024))
    assert git_service._should_full_clone("https://github.com/owner/repo.git") is True

    monkeypatch.setattr(git_service.httpx, "get", lambda url, **kw: FakeResp(2 * 1024 * 1024))
    git_service._SIZE_CACHE.clear()
    assert git_service._should_full_clone("https://github.com/owner/repo.git") is False

    def boom(url, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(git_service.httpx, "get", boom)
    git_service._SIZE_CACHE.clear()
    assert git_service._should_full_clone("https://github.com/owner/repo.git") is False

    # 非 GitHub 仓库不查 API，直接浅克隆
    assert git_service._should_full_clone("https://gitlab.com/owner/repo.git") is False

    # 阈值可配置
    monkeypatch.setenv("CODETRACE_CLONE_THRESHOLD_KB", str(50 * 1024))
    monkeypatch.setattr(git_service.httpx, "get", lambda url, **kw: FakeResp(100 * 1024))
    git_service._SIZE_CACHE.clear()
    assert git_service._should_full_clone("https://github.com/owner/repo.git") is False


def test_repo_size_cached(monkeypatch):
    """仓库体积结果缓存：重复查询不重复打 GitHub API。"""
    monkeypatch.setenv("GITHUB_TOKEN", "")
    git_service._SIZE_CACHE.clear()

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"size": 123456}

    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return FakeResp()

    monkeypatch.setattr(git_service.httpx, "get", fake_get)
    url = "https://github.com/owner/repo.git"
    assert git_service._repo_size_kb(url) == 123456
    assert git_service._repo_size_kb(url) == 123456
    assert len(calls) == 1
    git_service._SIZE_CACHE.clear()


def test_repo_size_url_validation(monkeypatch):
    """URL 解析严格：非完整 owner/repo 路径不请求 GitHub API。"""
    git_service._SIZE_CACHE.clear()
    calls = []
    monkeypatch.setattr(
        git_service.httpx,
        "get",
        lambda url, **kw: calls.append(url) or (_ for _ in ()).throw(AssertionError("不应请求 API")),
    )
    assert git_service._repo_size_kb("https://github.com/owner") is None
    assert git_service._repo_size_kb("https://github.com/") is None
    assert git_service._repo_size_kb("https://gitlab.com/owner/repo.git") is None
    assert calls == []
    git_service._SIZE_CACHE.clear()


def test_request_background_skips_when_fresh(local_repo, monkeypatch):
    """索引已新鲜时，后台触发直接跳过，不开新线程。"""
    assert index_service.ensure_indexed(local_repo)
    index_service._FRESH_CACHE.clear()

    calls = []
    monkeypatch.setattr(
        index_service, "request_index_build", lambda p: calls.append(p) or True
    )
    git_service._request_index_background(local_repo)
    assert calls == []


def test_index_status_sse_endpoint(local_repo, monkeypatch):
    """SSE 进度端点：能流式返回索引构建状态直到完成。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.trace import router

    # 让 URL → 路径推导指向本地测试仓库
    monkeypatch.setattr(git_service, "CACHE_DIR", local_repo.parent)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    index_service._FRESH_CACHE.clear()
    assert index_service.request_index_build(local_repo)

    resp = client.get(
        "/api/repo/index-status?repo_url=https://github.com/owner/repo.git",
        timeout=30,
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/event-stream")
    assert resp.headers.get("cache-control") == "no-cache"
    body = resp.text
    assert "data:" in body
    assert "[DONE]" in body


def test_build_thread_registry_cleaned(local_repo):
    """构建线程结束后从注册表移除，避免长期运行残留。"""
    index_service._FRESH_CACHE.clear()
    name = local_repo.name
    index_service._BUILD_THREADS.clear()

    assert index_service.request_index_build(local_repo) is True
    deadline = time.time() + 20
    while time.time() < deadline:
        status = index_service.get_index_status(local_repo)
        if status and status.get("status") in ("done", "error"):
            break
        time.sleep(0.2)
    # 等线程真正结束并完成清理
    while index_service._BUILD_THREADS.get(name) is not None and time.time() < deadline:
        time.sleep(0.2)
    assert index_service._BUILD_THREADS.get(name) is None


def test_index_status_sse_not_cloned(tmp_path, monkeypatch):
    """仓库尚未克隆：SSE 立即返回 not_found 并结束，不空转等待。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.trace import router

    monkeypatch.setattr(git_service, "CACHE_DIR", tmp_path / "no_such_cache")
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    resp = client.get(
        "/api/repo/index-status?repo_url=https://github.com/owner/repo.git",
    )
    assert resp.status_code == 200
    body = resp.text
    assert "not_found" in body
    assert "[DONE]" in body


def test_index_status_sse_not_started(local_repo, monkeypatch):
    """仓库存在但索引未启动：SSE 返回 not_started 立即结束，不空转 300s。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.trace import router

    monkeypatch.setattr(git_service, "CACHE_DIR", local_repo.parent)
    index_service._FRESH_CACHE.clear()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    resp = client.get(
        "/api/repo/index-status?repo_url=https://github.com/owner/repo.git",
        timeout=15,
    )
    assert resp.status_code == 200
    body = resp.text
    assert "not_started" in body
    assert "[DONE]" in body


def test_ensure_indexed_non_repo(tmp_path):
    assert index_service.ensure_indexed(tmp_path / "nope") is False
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    assert index_service.ensure_indexed(plain_dir) is False
