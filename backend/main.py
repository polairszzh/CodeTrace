import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.trace import router as trace_router

app = FastAPI(title="CodeTrace", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trace_router, prefix="/api", tags=["Trace"])


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)