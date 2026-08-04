from typing import Optional

from pydantic import BaseModel, Field


class TraceRequest(BaseModel):
    repo_url: str
    file_path: str


class DiffStats(BaseModel):
    additions: int
    deletions: int
    files_changed: int


class TimelineNode(BaseModel):
    commit_hash: str
    author: str
    date: str
    message: str
    pr_number: Optional[int] = None
    pr_title: Optional[str] = None
    change_type: str
    summary: str
    discussion_summary: Optional[str] = None
    diff_stats: Optional[DiffStats] = None


class TraceResponse(BaseModel):
    repo: str
    file_path: str
    timeline: list[TimelineNode]
    commit_count: int
    warnings: list[str] = Field(default_factory=list)
