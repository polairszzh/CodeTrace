"""安全配置测试 — CORS 来源限制。"""


def test_cors_restricted_origins():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    # 放行来源：返回 ACAO 头
    r = client.get("/api/repo/dashboard", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    # 未放行来源：不返回 ACAO 头
    r2 = client.get("/api/repo/dashboard", headers={"Origin": "https://evil.example.com"})
    assert r2.headers.get("access-control-allow-origin") is None
