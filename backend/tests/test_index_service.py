"""持久化预索引测试 — 本地小仓库：全量建索引、增量更新、过期回退、索引与 git 结果一致。"""

import datetime
import os
import subprocess

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


def test_ensure_indexed_non_repo(tmp_path):
    assert index_service.ensure_indexed(tmp_path / "nope") is False
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    assert index_service.ensure_indexed(plain_dir) is False
