"""异步耦合分析 runner — 在 asyncio 中通过线程池运行同步 git/LLM 操作"""

import asyncio

from services.git_service import clone_or_pull_repo, get_co_change_edges
from services.llm_service import LLMService


async def run_coupling_analysis(repo_url: str) -> dict:
    """
    运行 co-change 耦合分析（线程池中执行），返回前端可视化所需数据。

    总是返回合法的 dict，不抛异常，上层可放心 gather。
    """

    def _sync() -> dict:
        try:
            repo_path = clone_or_pull_repo(repo_url)
        except Exception as e:
            return {
                "nodes": [],
                "edges": [],
                "total_files": 0,
                "high_risk_count": 0,
                "note": f"仓库操作失败: {e}",
            }

        try:
            data = get_co_change_edges(repo_path, window_days=30)
        except Exception as e:
            return {
                "nodes": [],
                "edges": [],
                "total_files": 0,
                "high_risk_count": 0,
                "note": f"耦合分析失败: {e}",
            }

        if not data["nodes"]:
            return {
                "nodes": [],
                "edges": [],
                "total_files": 0,
                "high_risk_count": 0,
                "note": "数据不足，仓库活跃度较低或提交历史不够长",
            }

        # 构造 trend 列表用于 LLM 富化
        trends = []
        for n in data["nodes"]:
            trends.append({
                "file": n["id"],
                "recent_partners": n["recent_partners"],
                "old_partners": n["old_partners"],
                "coupling_growth": n["coupling_growth"],
                "boundary_crossings": n["boundary_crossings"],
                "risk": n["risk"],
            })

        try:
            llm = LLMService()
            enriched = llm.analyze_coupling_trends(trends)
        except Exception:
            enriched = trends  # LLM 失败时跳过富化

        # 合并 warning / suggestion 回 nodes
        by_file = {t["file"]: t for t in enriched}
        for node in data["nodes"]:
            ann = by_file.get(node["id"], {})
            node["warning"] = ann.get("warning", "")
            node["suggestion"] = ann.get("suggestion", "")

        high_risk = [n for n in data["nodes"] if n.get("risk") == "high"]
        return {
            "nodes": data["nodes"],
            "edges": data["edges"],
            "total_files": len(data["nodes"]),
            "high_risk_count": len(high_risk),
            "note": "coupling_growth > 0.3 且 recent_partners > 3 表示耦合面显著扩大",
        }

    return await asyncio.to_thread(_sync)
