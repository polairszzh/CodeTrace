from typing import Callable, Any
import json


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        execute: Callable,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.execute = execute

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }
    
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)
    
    def list_schemas(self) -> list[dict]:
        return [t.to_openai_schema() for t in self._tools.values()]
    
    def list_names(self) -> list[str]:
        return list(self._tools.keys())
    
    def list_descriptions(self) -> str:
        lines = []
        for t in self._tools.values():
            lines.append(f"- {t.name}: {t.description}")
        return "\n".join(lines)