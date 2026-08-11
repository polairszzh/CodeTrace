import hmac
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader

from routers.agent import router as agent_router
from routers.repo import router as repo_router
from routers.trace import router as trace_router

app = FastAPI(title="CodeTrace", version="1.0.0")


def _cors_settings(env_value: str) -> tuple[list[str], bool]:
    """
    CORS 配置（纯函数，便于测试）：
    默认仅放行本地前端来源；CODETRACE_CORS_ORIGINS 逗号分隔指定。
    CORS 规范：allow_origins="*" 不能与 allow_credentials=True 同用，配置 * 时自动关闭 credentials。
    """
    origins = [o.strip() for o in env_value.split(",") if o.strip()] or [
        "http://localhost:5173", "http://127.0.0.1:5173",
    ]
    return origins, "*" not in origins


_cors_origins, _cors_credentials = _cors_settings(os.getenv("CODETRACE_CORS_ORIGINS", ""))


def _api_key_config(env_value: str) -> str:
    """
    API Key 配置（纯函数，便于测试）：
    去除首尾空白；空值表示未配置（鉴权关闭），非空即启用。
    """
    return env_value.strip()


def _api_key_valid(configured: str, provided: str | None) -> bool:
    """校验请求头 X-API-Key；常量时间比较，防时序侧信道。"""
    if not configured:
        return True
    if not provided:
        return False
    # 先转 bytes 再比较：compare_digest 对含非 ASCII 的 str 会抛 TypeError
    return hmac.compare_digest(configured.encode("utf-8"), provided.encode("utf-8"))


_API_KEY = _api_key_config(os.getenv("CODETRACE_API_KEY", ""))


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(x_api_key: str | None = Security(api_key_header)):
    """
    统一鉴权依赖：配置了 CODETRACE_API_KEY 时，所有 /api 接口要求请求头
    X-API-Key 与配置值一致；未配置时放行（本地开发）。
    """
    if not _api_key_valid(_API_KEY, x_api_key):
        raise HTTPException(status_code=401, detail="无效或缺失的 API Key")
    return None

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    trace_router, prefix="/api", tags=["Trace"], dependencies=[Security(require_api_key)]
)
app.include_router(
    repo_router, prefix="/api", tags=["Repo"], dependencies=[Security(require_api_key)]
)
app.include_router(
    agent_router, prefix="/api", tags=["Agent"], dependencies=[Security(require_api_key)]
)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    # 安全默认：仅本机监听；需要局域网/远程访问时显式设置 CODETRACE_HOST=0.0.0.0
    host = os.getenv("CODETRACE_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)
