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
        tools_desc = self.tool_registry.list_descriptions()

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个代码仓库分析 Agent。用工具探索仓库，最后用中文总结。\n\n"
                    f"可用工具:\n{tools_desc}\n\n"
                    "规则:\n"
                    "- 每次回复只能做一件事：要么调用一个工具，要么给出最终总结\n"
                    "- 调用工具时，回复必须以 TOOL: 开头，后跟工具名和 JSON 参数:\n"
                    '  TOOL: repo_health {"repo_url": "...", "top_n": 3}\n'
                    "- 给出总结时，直接输出中文文本，不要带 TOOL: 前缀\n"
                    "- 调用 3-5 个工具后就该给总结了"
                ),
            },
            {"role": "user", "content": goal},
        ]
        steps = []

        for i in range(max_steps):
            response = self.llm._call(messages, temperature=0.2)
            text = (response or "").strip()

            if not text:
                steps.append({"step": i + 1, "error": "LLM 返回为空"})
                break

            if text.startswith("TOOL:"):
                try:
                    first_newline = text.index("\n")
                    tool_line = text[:first_newline]
                except ValueError:
                    tool_line = text
                tool_line = tool_line[5:].strip()
                parts = tool_line.split(" ", 1)
                tool_name = parts[0].strip()
                args_str = parts[1].strip() if len(parts) > 1 else "{}"
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    args = {}

                tool = self.tool_registry.get(tool_name)
                if not tool:
                    result = f"工具 {tool_name} 不存在，可用: {self.tool_registry.list_names()}"
                else:
                    try:
                        result = tool.execute(**args)
                    except Exception as e:
                        result = f"执行失败: {str(e)}"

                messages.append({"role": "assistant", "content": text})
                result_str = json.dumps(result, ensure_ascii=False, default=str)[:2000]
                messages.append({"role": "user", "content": f"工具返回: {result_str}"})
                steps.append({"step": i + 1, "tool": tool_name, "args": args, "result": result})
            else:
                steps.append({"step": i + 1, "final": text})
                break

        return steps
