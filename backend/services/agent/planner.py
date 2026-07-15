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
              "content": f"你是一个代码仓库分析 Agent。你可以使用以下工具来探索仓库:\n{self.tool_registry.list_descriptions()}\n\n根据用户的目标，一步步使用工具收集信息，最后用中文给出结构化的分析报告。"
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