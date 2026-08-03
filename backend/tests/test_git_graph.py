"""Git Graph 数据测试 — 分支拓扑 + 合入关系（本地合成仓库）。"""

import datetime
import os
import subprocess

from services import git_service
from services.git_service import get_git_graph


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


def _make_repo(tmp_path):
    """main: c1 → (分支 feature: c2) → c3 → merge feature(no-ff) → m1"""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True, capture_output=True)

    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _commit(repo, "c1", _date(3))

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True, capture_output=True)
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    _commit(repo, "c2", _date(2))

    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    (repo / "b.py").write_text("y = 1\n", encoding="utf-8")
    _commit(repo, "c3", _date(1))

    subprocess.run(
        ["git", "merge", "--no-ff", "feature", "-m", "merge feature (#5)"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


def _hashes(repo):
    out = subprocess.run(
        ["git", "rev-list", "main"],
        cwd=repo, capture_output=True, text=True,
    ).stdout.strip().split("\n")
    return [h for h in out if h]


def test_git_graph_branches(tmp_path):
    repo = _make_repo(tmp_path)
    data = get_git_graph(repo)

    assert data["default_branch"] == "main"
    names = {b["name"] for b in data["branches"]}
    assert names == {"main", "feature"}
    main_b = [b for b in data["branches"] if b["name"] == "main"][0]
    assert main_b["is_default"] is True
    assert main_b["total_commits"] == 4
    for b in data["branches"]:
        assert isinstance(b["ahead"], int)
        assert isinstance(b["behind"], int)
        assert b["head"]
        assert b["head_date"]


def test_git_graph_merges(tmp_path):
    repo = _make_repo(tmp_path)
    data = get_git_graph(repo)
    hashes = set(_hashes(repo))

    assert len(data["merges"]) >= 1
    merge = data["merges"][0]
    assert merge["message"] == "merge feature (#5)"
    assert len(merge["parents"]) == 2
    assert merge["hash"] in hashes


def test_git_graph_dag(tmp_path):
    repo = _make_repo(tmp_path)
    data = get_git_graph(repo)
    nodes = {n["id"]: n for n in data["graph"]["nodes"]}
    hashes = set(_hashes(repo))

    assert set(nodes) == hashes
    merge = [n for n in data["graph"]["nodes"] if n["is_merge"]]
    assert len(merge) == 1
    assert merge[0]["message"] == "merge feature (#5)"
    assert "main" in merge[0]["refs"]
    assert merge[0]["pr_number"] == 5
    assert len(merge[0]["parents"]) == 2

    # 普通提交无 PR 号
    assert all(n["pr_number"] is None for n in data["graph"]["nodes"] if not n["is_merge"])

    # 合入边的父关系
    edges = {(e["source"], e["target"]) for e in data["graph"]["edges"]}
    m1 = merge[0]["id"]
    assert any(s == m1 for s, _ in edges)
    parents_of_m1 = {t for s, t in edges if s == m1}
    assert len(parents_of_m1) == 2

    # feature 分支头标记
    feature_head = [b["head"] for b in data["branches"] if b["name"] == "feature"][0]
    assert "feature" in nodes[feature_head]["refs"]


def test_git_graph_cached(tmp_path):
    """git-graph 结果 5 分钟缓存：重复调用不重算，invalidate 后重算。"""
    repo = _make_repo(tmp_path)
    git_service._GIT_GRAPH_CACHE.clear()
    try:
        a = get_git_graph(repo)
        b = get_git_graph(repo)
        assert a is b  # 命中缓存（同一对象）
        git_service.invalidate_git_graph_cache(repo)
        c = get_git_graph(repo)
        assert c is not a  # 失效后重新计算
    finally:
        git_service._GIT_GRAPH_CACHE.clear()


def test_build_file_timeline_degrades_on_errors(tmp_path, monkeypatch):
    """timeline 构建中 LLM/GitHub 异常应降级而非整个请求 500。"""
    from routers import trace as trace_module

    repo = _make_repo(tmp_path)

    def boom(*a, **kw):
        raise RuntimeError("mock failure")

    monkeypatch.setattr(trace_module.github, "get_pr_info", boom)
    monkeypatch.setattr(trace_module.llm, "classify_and_summarize", boom)

    result = trace_module._build_file_timeline(repo, "alice/repo", "a.py")
    assert result is not None
    assert result.commit_count >= 1
    for node in result.timeline:
        assert node.change_type == "chore"
        assert node.summary  # 降级为 message 截断
        assert node.diff_stats is not None
