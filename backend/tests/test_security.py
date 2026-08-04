"""安全配置测试 — CORS 配置逻辑与 wiring。"""

import os


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
