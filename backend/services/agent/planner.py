import json
import os
from services.llm_service import LLMService


class AgentPlanner:
    def __init__(self, tool_registry, llm=None):
        self.tool_registry = tool_registry
        self.llm = llm or LLMService(
            api_key=os.getenv("LLM_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.getenv("LLM_MODEL", "deepseek-v4-pro"),
        )

    def run(self, goal: str, max_steps: int = 20) -> list[dict]:
      messages = [
          {
              "role": "system",
              "content": (
                  "你是一个代码仓库分析 Agent。你可以使用工具探索仓库。\n\n"
                  f"可用工具:\n{self.tool_registry.list_descriptions()}\n\n"
                  "分析策略:\n"
                  "1. 首先用 repo_health 或 git_hotspots 扫描项目热点\n"
                  "2. 用 file_bulk_summary 批量了解热点文件的活跃程度\n"
                  "3. 对 bug 修复率超过 30% 的文件，用 trace_function 深度追溯关键函数\n"
                  "4. 对关键 commit，用 pr_info 查看 PR 讨论背景\n\n"
                  "最终报告格式 (JSON only，不要其他文本):\n"
                  '{"repo": "仓库名",\n'
                  ' "hotspots": [{"file": "路径", "changes": 次数, "bug_ratio": 比例}],\n'
                  ' "at_risk": [{"file": "路径", "reason": "风险原因"}],\n'
                  ' "stable_modules": ["稳定模块..."],\n'
                  ' "summary": "一段中文总结",\n'
                  ' "recommendations": ["建议1", "建议2"]}'
              ),
          },
          {"role": "user", "content": goal},
      ]
      steps = []

      for i in range(max_steps):
        response = self.llm._call_with_tools(
        messages=messages,
        tools=self.tool_registry.list_schemas(),
        )

        tool_calls = response.get("tool_calls")
        if tool_calls:
            tc = tool_calls[0]
            tool_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}

            tool = self.tool_registry.get(tool_name)
            result = tool.execute(**args) if tool else f"工具 {tool_name} 不存在"

            messages.append(response)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})

            steps.append({"step": i + 1, "tool": tool_name, "args": args, "result": result})
        else:
            answer = response.get("content", "")
            steps.append({"step": i + 1, "final": answer})
            break

      return steps