from models.schemas import TraceRequest, DiffStats, TimelineNode, TraceResponse


def test_trace_request_model():
    req = TraceRequest(
        repo_url="https://github.com/vercel/next.js",
        file_path="packages/next/server/web/sandbox.ts"
    )
    assert req.repo_url == "https://github.com/vercel/next.js"
    assert req.file_path == "packages/next/server/web/sandbox.ts"


def test_diff_stats():
    stats = DiffStats(additions=10, deletions=5, files_changed=3)
    assert stats.additions == 10
    assert stats.deletions == 5
    assert stats.files_changed == 3


def test_timeline_node():
    stats = DiffStats(additions=10, deletions=5, files_changed=3)
    node = TimelineNode(
        commit_hash="a1b2c3d",
        author="alice",
        date="2025-03-15T14:30:00Z",
        message="feat: add payment support",
        pr_number=42,
        pr_title="Add payment module",
        change_type="feature",
        summary="新增支付功能",
        diff_stats=stats,
    )
    assert node.commit_hash == "a1b2c3d"
    assert node.pr_number == 42
    assert node.change_type == "feature"


def test_trace_response():
    node = TimelineNode(
        commit_hash="a1b2c3d",
        author="alice",
        date="2025-03-15T14:30:00Z",
        message="feat: add payment support",
        change_type="feature",
        summary="新增支付功能",
    )
    resp = TraceResponse(
        repo="vercel/next.js",
        file_path="packages/next/server.ts",
        timeline=[node],
        commit_count=1,
    )
    assert resp.commit_count == 1
    assert len(resp.timeline) == 1
