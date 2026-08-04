import os
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.trace import router as trace_router

app = FastAPI(title="CodeTrace", version="1.0.0")

# CORS：默认仅放行本地前端来源；部署时用 CODETRACE_CORS_ORIGINS（逗号分隔）指定
_cors_origins = [
    o.strip() for o in os.getenv("CODETRACE_CORS_ORIGINS", "").split(",") if o.strip()
] or ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
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
