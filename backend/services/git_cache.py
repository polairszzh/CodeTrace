"""缓存触发层 — 索引后台构建触发与 Git Graph 内存缓存失效。"""

import logging
from pathlib import Path

from services import index_service

logger = logging.getLogger(__name__)

_GIT_GRAPH_CACHE: dict[str, tuple[float, dict]] = {}
_GIT_GRAPH_TTL = 300  # 秒，Dashboard 重复打开/刷新免重复计算


def _request_index_background(repo_path: Path):
    """后台异步补索引（不阻塞请求路径；失败静默，查询走 git 回退）。"""
    try:
        if index_service.index_fresh(repo_path):
            return  # 已新鲜，避免每个请求都开线程
        index_service.request_index_build(repo_path)
    except Exception:
        pass


def invalidate_git_graph_cache(repo_path: Path):
    """仓库发生 clone/pull 后清空对应缓存。"""
    try:
        _GIT_GRAPH_CACHE.pop(str(Path(repo_path).resolve()), None)
    except Exception:
        pass
