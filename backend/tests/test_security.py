"""安全配置测试 — CORS 配置逻辑与 wiring。"""

import os

from fastapi import FastAPI, Security
from fastapi.testclient import TestClient


def test_cors_settings_default():
    """未配置环境变量时默认仅放行本地前端来源，credentials 开启。"""
    from main import _cors_settings

    origins, creds = _cors_settings("")
    assert origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert creds is True


def test_cors_settings_wildcard_disables_credentials():
    """CODETRACE_CORS_ORIGINS=* 时自动关闭 credentials（符合 CORS 规范）。"""
    from main import _cors_settings

    origins, creds = _cors_settings("*")
    assert origins == ["*"]
    assert creds is False


def test_cors_settings_custom_origins():
    """逗号分隔自定义来源。"""
    from main import _cors_settings

    origins, creds = _cors_settings("http://a.example.com, http://b.example.com")
    assert origins == ["http://a.example.com", "http://b.example.com"]
    assert creds is True


def test_app_cors_matches_settings():
    """App 中间件配置与 _cors_settings(当前环境) 一致（不依赖具体环境值）。"""
    import main

    expected_origins, expected_creds = main._cors_settings(
        os.getenv("CODETRACE_CORS_ORIGINS", "")
    )
    cors = [m for m in main.app.user_middleware if m.cls.__name__ == "CORSMiddleware"][0]
    assert cors.kwargs["allow_origins"] == expected_origins
    assert cors.kwargs["allow_credentials"] is expected_creds


def test_api_key_config_empty_disables_auth():
    """未配置 CODETRACE_API_KEY 时鉴权关闭。"""
    from main import _api_key_config

    assert _api_key_config("") == ""


def test_api_key_config_configured_enables_auth():
    """配置后启用鉴权，且去除首尾空白。"""
    from main import _api_key_config

    assert _api_key_config("  secret-123  ") == "secret-123"


def test_api_key_valid_never_configured_allows_all():
    """未配置时无论是否带 key 都放行。"""
    from main import _api_key_valid

    assert _api_key_valid("", None) is True
    assert _api_key_valid("", "anything") is True


def test_api_key_valid_rejects_missing_and_wrong():
    """配置后缺失或错误 key 均拒绝，正确 key 放行。"""
    from main import _api_key_valid

    assert _api_key_valid("secret-123", None) is False
    assert _api_key_valid("secret-123", "") is False
    assert _api_key_valid("secret-123", "secret-1234") is False
    assert _api_key_valid("secret-123", "SECRET-123") is False
    assert _api_key_valid("secret-123", "secret-123") is True


def test_api_key_valid_non_ascii_header_does_not_raise():
    """非 ASCII 的 X-API-Key 不应抛 TypeError（返回拒绝而非 500）。"""
    from main import _api_key_valid

    assert _api_key_valid("secret-123", "é") is False
    assert _api_key_valid("é", "é") is True


def test_api_key_dependency_enforced_when_configured(monkeypatch):
    """配置 key 后，缺头/错头 401，正确头 200。"""
    import main

    monkeypatch.setattr(main, "_API_KEY", "secret-123")
    app = FastAPI()

    @app.get("/api/ping", dependencies=[Security(main.require_api_key)])
    def ping():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/ping").status_code == 401
    assert client.get("/api/ping", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/ping", headers={"X-API-Key": "secret-123"}).status_code == 200


def test_api_key_dependency_open_when_not_configured(monkeypatch):
    """未配置 key 时依赖直接放行，本地开发不阻塞。"""
    import main

    monkeypatch.setattr(main, "_API_KEY", "")
    app = FastAPI()

    @app.get("/api/ping", dependencies=[Security(main.require_api_key)])
    def ping():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/ping").status_code == 200


def test_real_app_blocks_without_api_key(monkeypatch):
    """main.app 实际 wiring：配置 key 后缺失 key 的真实请求 401。"""
    import main

    monkeypatch.setattr(main, "_API_KEY", "secret-123")
    client = TestClient(main.app)
    # 该用例验证真实 app 的 router 级鉴权接线：期望 401（鉴权拦截）；
    # 若路由被移除会得到 404，说明本用例覆盖的接线已失效，需同步更新
    resp = client.post("/api/trace", json={})
    assert resp.status_code == 401, f"期望 401（鉴权拦截），实际 {resp.status_code}"


def test_real_app_openapi_declares_api_key_security():
    """main.app 的 OpenAPI 对每个 /api 操作声明 X-API-Key 安全要求（Security 接线）。"""
    import main

    schema = main.app.openapi()
    schemes = schema.get("components", {}).get("securitySchemes", {})
    api_key_schemes = [
        name
        for name, spec in schemes.items()
        if spec.get("type") == "apiKey"
        and spec.get("in") == "header"
        and spec.get("name") == "X-API-Key"
    ]
    assert api_key_schemes, "OpenAPI 应声明 X-API-Key 的 securityScheme"
    api_paths = [p for p in schema["paths"] if p.startswith("/api")]
    assert api_paths, "应存在 /api 路径"
    for path in api_paths:
        for method, op in schema["paths"][path].items():
            if method in ("parameters",):
                continue
            op_security = op.get("security") or []
            assert any(
                scheme in op_sec
                for op_sec in op_security
                for scheme in api_key_schemes
            ), (
                f"{method.upper()} {path} 缺少 API Key 安全要求"
            )


def test_healthz_public_without_api_key(monkeypatch):
    """配置 API Key 后 /healthz 仍免鉴权（容器健康检查可用）。"""
    import main

    monkeypatch.setattr(main, "_API_KEY", "secret-123")
    client = TestClient(main.app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
