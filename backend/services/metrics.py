"""共享统计计算 — git_service 与 index_service 双轨收敛的公共实现。

职责边界：各 service 只负责「从各自数据源获取窗口分组/原始行」，
本模块负责纯计算（co-change 指标、耦合风险分级、健康度时效评分与组装），
确保两条实现产出完全一致的结果，避免双轨漂移。
"""

from collections import Counter, defaultdict
from datetime import datetime

# ── 耦合风险分级 ────────────────────────────────────────


def classify_coupling_risk(rec_partners: int, partner_delta: int) -> str:
    """耦合风险分级：伙伴数 ≥8 且增量 ≥5 → high；≥4 且增量 ≥2 → medium；否则 low。"""
    if rec_partners >= 8 and partner_delta >= 5:
        return "high"
    if rec_partners >= 4 and partner_delta >= 2:
        return "medium"
    return "low"


def _module(path: str) -> str:
    return path.replace("\\", "/").split("/")[0] if "/" in path or "\\" in path else ""


def build_metrics(groups: list[set[str]]) -> dict:
    """每组文件集 → {file: {partners, bx}}（伙伴数与跨模块共现次数）。"""
    partner_map = defaultdict(set)
    bx_counter = Counter()
    for files in groups:
        flist = list(files)
        for i, fa in enumerate(flist):
            for fb in flist[i + 1:]:
                partner_map[fa].add(fb)
                partner_map[fb].add(fa)
                ma = _module(fa)
                mb = _module(fb)
                if ma and mb and ma != mb:
                    bx_counter[fa] += 1
                    bx_counter[fb] += 1
    return {f: {"partners": len(p), "bx": bx_counter.get(f, 0)} for f, p in partner_map.items()}


def compute_cochange_trends(recent_groups, old_groups, limit: int = 30) -> list[dict]:
    """双窗口 co-change 趋势，输出与旧版 git_service/index_service 完全一致。"""
    recent_m = build_metrics(recent_groups)
    old_m = build_metrics(old_groups)
    results = []
    for f in set(recent_m) | set(old_m):
        rec = recent_m.get(f, {"partners": 0, "bx": 0})
        old = old_m.get(f, {"partners": 0, "bx": 0})
        if rec["partners"] == 0 and old["partners"] == 0:
            continue
        old_p = old["partners"] or 1
        growth = round((rec["partners"] - old["partners"]) / old_p, 2)
        results.append({
            "file": f,
            "recent_partners": rec["partners"],
            "old_partners": old["partners"],
            "coupling_growth": growth,
            "boundary_crossings": rec["bx"],
            "risk": classify_coupling_risk(rec["partners"], rec["partners"] - old["partners"]),
        })
    results.sort(key=lambda x: (-x["coupling_growth"], -x["boundary_crossings"]))
    return results[:limit]


def compute_cochange_edges(recent_groups, old_groups, limit: int = 30, edge_limit: int = 200) -> dict:
    """双窗口 co-change 边（nodes + edges），输出与旧版完全一致。"""
    edge_counter = Counter()
    recent_map = defaultdict(set)
    bx_counter = Counter()
    for files in recent_groups:
        flist = list(files)
        for i, fa in enumerate(flist):
            for fb in flist[i + 1:]:
                key = tuple(sorted([fa, fb]))
                edge_counter[key] += 1
                recent_map[fa].add(fb)
                recent_map[fb].add(fa)
                ma = _module(fa)
                mb = _module(fb)
                if ma and mb and ma != mb:
                    bx_counter[fa] += 1
                    bx_counter[fb] += 1

    old_map = defaultdict(set)
    for files in old_groups:
        flist = list(files)
        for i, fa in enumerate(flist):
            for fb in flist[i + 1:]:
                old_map[fa].add(fb)
                old_map[fb].add(fa)

    nodes = []
    for f in set(recent_map) | set(old_map):
        rec_p = len(recent_map.get(f, set()))
        old_p = len(old_map.get(f, set()))
        if rec_p == 0 and old_p == 0:
            continue
        denom = old_p or 1
        growth = round((rec_p - old_p) / denom, 2)
        nodes.append({
            "id": f,
            "label": f.split("/")[-1].split("\\")[-1],
            "module": _module(f),
            "recent_partners": rec_p,
            "old_partners": old_p,
            "coupling_growth": growth,
            "boundary_crossings": bx_counter.get(f, 0),
            "risk": classify_coupling_risk(rec_p, rec_p - old_p),
        })
    nodes.sort(key=lambda x: (-x["coupling_growth"], -x["boundary_crossings"]))
    top_nodes = nodes[:limit]
    top_ids = {n["id"] for n in top_nodes}

    edges = []
    for (fa, fb), weight in edge_counter.most_common(edge_limit):
        if fa in top_ids and fb in top_ids:
            edges.append({"source": fa, "target": fb, "weight": weight})
    return {"nodes": top_nodes, "edges": edges}


# ── 健康度：时效评分与结果组装 ──────────────────────────

# 注意：顺序敏感——SQL CASE 分支按此顺序生成，先命中先得分；
# 调整桶阈值/分数时需同时保持 Python 与 SQL 语义一致
_RECENCY_BUCKETS = ((7, 10), (30, 5), (90, 2), (None, 0.5))


def recency_score_for_dates(dates, now: datetime | None = None) -> float:
    """按提交日期列表计算时效分数（桶阈值由 _RECENCY_BUCKETS 驱动，与 SQL 共用防漂移）。"""
    now = now or datetime.now()
    score = 0.0
    for d in dates:
        try:
            dt = datetime.strptime(str(d)[:10], "%Y-%m-%d")
            days_ago = (now - dt).days
        except Exception:
            days_ago = None
        if days_ago is None:
            days_ago = float("inf")  # 非法日期 → 落入兜底桶（0.5）
        for bucket_days, bucket_score in _RECENCY_BUCKETS:
            if bucket_days is None or days_ago <= bucket_days:
                score += bucket_score
                break
    return score


def _recency_bucket_sql(date_expr: str) -> str:
    """内部工具：生成与 recency_score_for_dates 同桶阈值的 SQL CASE。

    date_expr 必须为可信常量表达式（仅内部以固定 substr 调用），勿对用户输入拼接。
    """
    cases = []
    for days, score in _RECENCY_BUCKETS:
        if days is None:
            cases.append(f"ELSE {score}")
        else:
            cases.append(
                f"WHEN {date_expr} >= date('now', 'localtime', '-{days} days') THEN {score}"
            )
    return "SUM(CASE " + " ".join(cases) + " END)"


def assemble_health_stats(rows, recency_map, top_n: int = 20, messages_of=None) -> list[dict]:
    """健康度结果组装：churn × recency 综合排序。rows 为含 file/total_* 的 dict。"""
    stats = []
    for r in rows:
        total = r["total_commits"]
        if total == 0:
            continue
        add = r["total_additions"]
        dele = r["total_deletions"]
        authors = r["authors"]
        if isinstance(authors, str):
            authors_list = [a for a in authors.split(",") if a]  # 过滤空串，与 None 一致返回 []
        elif authors:
            authors_list = list(authors)
        else:
            authors_list = []
        stats.append({
            "file": r["file"],
            "total_commits": total,
            "total_additions": add,
            "total_deletions": dele,
            "churn": add + dele,
            "recency_score": round(recency_map.get(r["file"], 0.5), 1),
            "commit_messages": [],
            "top_authors": authors_list[:3],
        })
    stats.sort(key=lambda x: x["churn"] * x["recency_score"], reverse=True)
    top = stats[:top_n]
    if messages_of:
        msgs_map = messages_of([s["file"] for s in top])
        for s in top:
            s["commit_messages"] = msgs_map.get(s["file"], [])
    return top
