"""Agent 工具相关阈值测试 — 耦合风险分级参数化。"""

import pytest

from services.metrics import classify_coupling_risk


@pytest.mark.parametrize("partners,delta,expected", [
    (8, 5, "high"),
    (9, 6, "high"),
    (10, 5, "high"),
    (8, 4, "medium"),
    (7, 5, "medium"),
    (4, 2, "medium"),
    (4, 1, "low"),
    (3, 2, "low"),
    (0, 0, "low"),
    (8, -1, "low"),
])
def test_classify_coupling_risk(partners, delta, expected):
    assert classify_coupling_risk(partners, delta) == expected
