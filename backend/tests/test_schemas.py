from models.schemas import TraceRequest

def test_trace_request_model():
    # Create an instance of TraceRequest
    req = TraceRequest(
        repo_url="https://github.com/vercel/next.js",
        file_path="packages/next/server/web/sandbox.ts"
    )

    # Assert that the attributes are set correctly
    assert req.repo_url == "https://github.com/vercel/next.js"
    assert req.file_path == "packages/next/server/web/sandbox.ts"