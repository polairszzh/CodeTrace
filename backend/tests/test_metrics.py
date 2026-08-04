"""共享统计计算模块测试。"""

import datetime

from services import metrics


def test_assemble_health_stats_authors_none():
    """authors 为 None 时不抛错（GROUP_CONCAT 可能返回 NULL）。"""
    rows = [
        {"file": "a.py", "total_commits": 2, "total_additions": 5,
         "total_deletions": 1, "authors": None},
    ]
    top = metrics.assemble_health_stats(rows, {"a.py": 5.0}, top_n=10)
    assert top[0]["file"] == "a.py"
    assert top[0]["top_authors"] == []
    assert top[0]["churn"] == 6


def test_assemble_health_stats_messages_batch():
    """messages_of 按文件列表批量返回 {file: [messages]}。"""
    rows = [
        {"file": "a.py", "total_commits": 1, "total_additions": 1,
         "total_deletions": 0, "authors": "x"},
    ]
    top = metrics.assemble_health_stats(
        rows, {"a.py": 5.0}, top_n=10,
        messages_of=lambda files: {"a.py": ["msg1", "msg2"]},
    )
    assert top[0]["commit_messages"] == ["msg1", "msg2"]


def test_recency_bucket_sql_matches_python():
    """SQL 桶阈值与 Python 评分共用常量（7/30/90 → 10/5/2，其余 0.5）。"""
    sql = metrics._recency_bucket_sql()
    assert "'-7 days') THEN 10" in sql
    assert "'-30 days') THEN 5" in sql
    assert "'-90 days') THEN 2" in sql
    assert "ELSE 0.5" in sql
    future = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime(
        "%Y-%m-%d 12:00:00 +0800"
    )
    assert metrics.recency_score_for_dates([future]) == 10.0
    assert metrics.recency_score_for_dates([]) == 0.0
