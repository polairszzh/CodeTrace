"""安全配置测试 — CORS 来源限制。"""


def test_cors_restricted_origins(monkeypatch):
    import importlib

    import main

    monkeypatch.delenv("CODETRACE_CORS_ORIGINS", raising=False)
    main = importlib.reload(main)  # 固定环境，避免外部配置影响断言
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    # 放行来源：返回 ACAO 头
    r = client.get("/api/repo/dashboard", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    # 未放行来源：不返回 ACAO 头
    r2 = client.get("/api/repo/dashboard", headers={"Origin": "https://evil.example.com"})
    assert r2.headers.get("access-control-allow-origin") is None


def test_cors_wildcard_disables_credentials(monkeypatch):
    """CODETRACE_CORS_ORIGINS=* 时 allow_credentials 自动关闭（符合 CORS 规范）。"""
    import importlib

    import main

    monkeypatch.setenv("CODETRACE_CORS_ORIGINS", "*")
    main = importlib.reload(main)
    middleware = main.app.user_middleware
    cors = [m for m in middleware if m.cls.__name__ == "CORSMiddleware"][0]
    assert cors.kwargs["allow_origins"] == ["*"]
    assert cors.kwargs["allow_credentials"] is False
