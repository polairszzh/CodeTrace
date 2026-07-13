from pydantic import BaseModel

class TraceRequest(BaseModel):
    repo_url: str
    file_path: str