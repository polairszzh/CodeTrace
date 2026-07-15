from dotenv import load_dotenv

load_dotenv()

from services.agent import registry
from services.agent.planner import AgentPlanner


def test_planner_simple_goal():
    planner = AgentPlanner(registry)
    steps = planner.run(
        "分析仓库 https://github.com/polairszzh/CodeTrace.git ，找出变更最频繁的 3 个文件",
        max_steps=5,
    )
    assert len(steps) > 0
    # 至少应该有一个 git_hotspots 调用
    tool_names = [s["tool"] for s in steps if "tool" in s]
    assert "git_hotspots" in tool_names
