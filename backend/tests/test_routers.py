"""repo/* 路由测试 — 使用本地合成仓库（不依赖网络）。"""

import datetime
import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from services import git_service


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
def repo_env(tmp_path, monkeypatch):
    """本地仓库按缓存目录命名规则放置，并隔离索引目录。"""
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(git_service, "CACHE_DIR", cache)
    monkeypatch.setattr(git_service, "_LOCK_DIR", cache / ".locks")

    repo = cache / "github.com_owner_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True, capture_output=True)
    (repo / "a.py").write_text("def alpha():\n    return 1\n\nclass Beta:\n    pass\n", encoding="utf-8")
    _commit(repo, "feat: init (#1)", _date(3))
    (repo / "a.py").write_text(
        "def alpha():\n    return 2\n\ndef delta():\n    return 3\n\nclass Beta:\n    pass\n",
        encoding="utf-8",
    )
    _commit(repo, "feat: more (#2)", _date(1))
    return cache


URL = "https://github.com/owner/repo.git"


def test_repo_files(repo_env):
    from main import app
    r = TestClient(app).get("/api/repo/files", params={"repo_url": URL})
    assert r.status_code == 200
    names = {e["name"] for e in r.json()["entries"]}
    assert "a.py" in names


def test_repo_symbols(repo_env):
    from main import app
    r = TestClient(app).get("/api/repo/symbols", params={"repo_url": URL, "file_path": "a.py"})
    assert r.status_code == 200
    data = r.json()
    assert {f["name"] for f in data["functions"]} == {"alpha", "delta"}
    assert [c["name"] for c in data["classes"]] == ["Beta"]


def test_repo_file_risks(repo_env):
    from main import app
    r = TestClient(app).get("/api/repo/file-risks", params={"repo_url": URL})
    assert r.status_code == 200
    risks = r.json()["risks"]
    assert "a.py" in risks
    assert risks["a.py"] in ("high", "medium", "low")


def test_repo_dashboard(repo_env):
    from main import app
    r = TestClient(app).get("/api/repo/dashboard", params={"repo_url": URL})
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["total_commits"] == 2
    assert "risk_distribution" in data


def test_repo_git_graph(repo_env):
    from main import app
    r = TestClient(app).get("/api/repo/git-graph", params={"repo_url": URL})
    assert r.status_code == 200
    data = r.json()
    assert data["default_branch"] == "main"
    assert len(data["graph"]["nodes"]) == 2


def test_repo_tracking(repo_env):
    from main import app
    r = TestClient(app).get("/api/repo/tracking", params={"repo_url": URL}, timeout=20)
    assert r.status_code == 200
    assert "status" in r.json()


def test_repo_pr_info(repo_env, monkeypatch):
    from main import app
    from routers import trace as trace_module

    monkeypatch.setattr(
        trace_module.github, "get_pr_info",
        lambda repo_full, pr_number: {"title": "t", "state": "open", "author": "x"},
    )
    r = TestClient(app).get("/api/repo/pr-info", params={"repo_url": URL, "pr_number": 1})
    assert r.status_code == 200
    assert r.json()["title"] == "t"


def test_repo_pr_info_invalid_url():
    from main import app
    r = TestClient(app).get("/api/repo/pr-info", params={"repo_url": "not-a-url", "pr_number": 1})
    assert r.status_code == 400


def test_repo_index_status_uncloned(tmp_path, monkeypatch):
    """未克隆仓库：SSE 立即返回 not_found。"""
    from main import app
    monkeypatch.setattr(git_service, "CACHE_DIR", tmp_path / "no_cache")
    r = TestClient(app).get("/api/repo/index-status", params={"repo_url": URL})
    assert r.status_code == 200
    assert "not_found" in r.text
    assert "[DONE]" in r.text
