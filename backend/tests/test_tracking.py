"""持续追踪服务测试 — 快照/增量/幂等/LLM 降级/后台触发。"""

import datetime
import os
import subprocess
import time

from services import tracking_service


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


def _make_repo(tmp_path, commits):
    """commits: [(message, days_ago), ...]"""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True, capture_output=True)
    for i, (msg, days) in enumerate(commits):
        (repo / f"f{i}.py").write_text(f"# {msg}\n", encoding="utf-8")
        _commit(repo, msg, _date(days))
    return repo


class FakeLLM:
    def __init__(self, text=None, raise_on_call=False):
        self.text = text or "## 增量报告\n这是一段足够长的 mock 增量报告内容，用于通过长度校验。"
        self.raise_on_call = raise_on_call

    def _call(self, messages, temperature=None):
        if self.raise_on_call:
            raise RuntimeError("mock LLM failure")
        return self.text


def test_baseline_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    data = tracking_service.refresh_tracking(repo, llm=FakeLLM())

    assert len(data["snapshots"]) == 1
    report = data["latest_report"]
    assert report["generated_by"] == "baseline"
    assert "基线" in report["markdown"]
    assert data["snapshots"][0]["head"]
    assert tracking_service.get_tracking(repo)["stale"] is False


def test_incremental_delta_and_prs(tmp_path, monkeypatch):
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    tracking_service.refresh_tracking(repo, llm=FakeLLM())

    (repo / "hot.py").write_text("x = 1\n", encoding="utf-8")
    _commit(repo, "feat: hot (#2)", _date(2))
    (repo / "hot.py").write_text("x = 2\n", encoding="utf-8")
    _commit(repo, "fix: hot (#3)", _date(1))

    data = tracking_service.refresh_tracking(repo, llm=FakeLLM())
    assert len(data["snapshots"]) == 2
    report = data["latest_report"]
    assert report["generated_by"] == "llm"
    assert report["head"] != data["snapshots"][0]["head"]
    delta = report["structured"]
    assert delta["baseline"] is False
    assert delta["new_commits"] == 2
    pr_numbers = [p["pr_number"] for p in delta["new_prs"]]
    assert pr_numbers == [3, 2]
    assert any(c["file"] == "hot.py" for c in delta["new_hot_files"])
    assert delta["totals_delta"]["commits"] == 2


def test_pr_extraction_merge_message(tmp_path, monkeypatch):
    """兼容 GitHub「Merge pull request #N」提交信息。"""
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    tracking_service.refresh_tracking(repo, llm=FakeLLM())
    (repo / "f0.py").write_text("# changed\n", encoding="utf-8")
    _commit(repo, "Merge pull request #7 from feature/x", _date(1))

    data = tracking_service.refresh_tracking(repo, llm=FakeLLM())
    delta = data["latest_report"]["structured"]
    assert [p["pr_number"] for p in delta["new_prs"]] == [7]


def test_pr_dedupe_same_pr_multiple_commits(tmp_path, monkeypatch):
    """同一 PR 多个提交只计一次。"""
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    tracking_service.refresh_tracking(repo, llm=FakeLLM())
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _commit(repo, "feat: part1 (#2)", _date(2))
    (repo / "b.py").write_text("y = 1\n", encoding="utf-8")
    _commit(repo, "feat: part2 (#2)", _date(1))

    data = tracking_service.refresh_tracking(repo, llm=FakeLLM())
    prs = data["latest_report"]["structured"]["new_prs"]
    assert [p["pr_number"] for p in prs] == [2]
    assert len(prs) == 1


def test_commit_message_with_pipe_kept(tmp_path, monkeypatch):
    """提交信息含 | 不被截断。"""
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    tracking_service.refresh_tracking(repo, llm=FakeLLM())
    old_head = tracking_service.get_tracking(repo)["head"]
    (repo / "f0.py").write_text("# changed\n", encoding="utf-8")
    _commit(repo, "feat: pipe | in message (#5)", _date(1))

    commits = tracking_service._new_commits(repo, old_head)
    assert commits and commits[0]["message"] == "feat: pipe | in message (#5)"


def test_delta_error_skips_snapshot(tmp_path, monkeypatch):
    """git 区间不可读（delta error）时不追加快照、不生成误导报告。"""
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    tracking_service.refresh_tracking(repo, llm=FakeLLM())

    (repo / "f0.py").write_text("# changed\n", encoding="utf-8")
    _commit(repo, "feat: next (#2)", _date(1))
    monkeypatch.setattr(tracking_service, "_new_commits", lambda *a, **kw: None)
    monkeypatch.setattr(tracking_service, "_range_churn", lambda *a, **kw: None)
    data = tracking_service.refresh_tracking(repo, llm=FakeLLM())
    assert len(data["snapshots"]) == 1
    assert data["latest_report"]["generated_by"] == "baseline"  # 报告未被覆盖


def test_refresh_error_backoff(tmp_path, monkeypatch):
    """刷新失败后记录 refresh_error，5 分钟内不再重复触发后台任务。"""
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    tracking_service._TRACKING_THREADS.clear()
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    tracking_service.refresh_tracking(repo, llm=FakeLLM())

    (repo / "f0.py").write_text("# changed\n", encoding="utf-8")
    _commit(repo, "feat: next (#2)", _date(1))
    monkeypatch.setattr(tracking_service, "_new_commits", lambda *a, **kw: None)
    monkeypatch.setattr(tracking_service, "_range_churn", lambda *a, **kw: None)

    data = tracking_service.refresh_tracking(repo, llm=FakeLLM())
    assert data.get("refresh_error") is not None
    assert tracking_service.request_tracking(repo) is False  # 退避中

    # 模拟退避窗口已过（清除错误标记）→ 可再次触发
    store = tracking_service._load(repo)
    store.pop("refresh_error", None)
    tracking_service._save(repo, store)
    monkeypatch.setattr(tracking_service, "_new_commits", lambda *a, **kw: [])
    monkeypatch.setattr(tracking_service, "_range_churn", lambda *a, **kw: [])
    assert tracking_service.request_tracking(repo) is True


def test_empty_repo_no_crash(tmp_path, monkeypatch):
    """空仓库（无提交）不崩溃，不产生快照。"""
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    data = tracking_service.refresh_tracking(repo, llm=FakeLLM())
    assert data["snapshots"] == []
    info = tracking_service.get_tracking(repo)
    assert info["head"] is None


def test_git_range_error_returns_none(tmp_path, monkeypatch):
    """git 区间不可读时返回 None 而非静默空列表。"""
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    assert tracking_service._new_commits(repo, "deadbeef00000000000000000000000000000000") is None
    assert tracking_service._range_churn(repo, "deadbeef00000000000000000000000000000000") is None


def test_idempotent_no_new_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    tracking_service.refresh_tracking(repo, llm=FakeLLM())
    data = tracking_service.refresh_tracking(repo, llm=FakeLLM())
    assert len(data["snapshots"]) == 1


def test_llm_failure_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    tracking_service.refresh_tracking(repo, llm=FakeLLM())
    (repo / "hot.py").write_text("x = 1\n", encoding="utf-8")
    _commit(repo, "feat: hot (#2)", _date(1))
    data = tracking_service.refresh_tracking(repo, llm=FakeLLM(raise_on_call=True))
    report = data["latest_report"]
    assert report["generated_by"] == "fallback"
    assert "变更概览" in report["markdown"]


def test_request_tracking_background(tmp_path, monkeypatch):
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    tracking_service._TRACKING_THREADS.clear()
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    assert tracking_service.request_tracking(repo) is True

    deadline = time.time() + 20
    while time.time() < deadline:
        data = tracking_service.get_tracking(repo)
        if data.get("latest_report") is not None:
            break
        time.sleep(0.2)
    assert data.get("latest_report") is not None
    assert data["stale"] is False


def test_get_tracking_stale_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("CODETRACE_INDEX_DIR", str(tmp_path / "index"))
    repo = _make_repo(tmp_path, [("feat: init (#1)", 5)])
    tracking_service.refresh_tracking(repo, llm=FakeLLM())
    assert tracking_service.get_tracking(repo)["stale"] is False

    (repo / "f0.py").write_text("# changed\n", encoding="utf-8")
    _commit(repo, "feat: more (#2)", _date(1))
    assert tracking_service.get_tracking(repo)["stale"] is True
