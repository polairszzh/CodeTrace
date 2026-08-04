import os
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trace_router, prefix="/api", tags=["Trace"])


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    # 安全默认：仅本机监听；需要局域网/远程访问时显式设置 CODETRACE_HOST=0.0.0.0
    host = os.getenv("CODETRACE_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)
