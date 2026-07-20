import httpx
import json


class LLMService:
    """
    OpenAI 接口兼容的 LLM 客户端
    可切换 OpenAI/Claude Code/Qwen/Deepseek/本地 Ollama 等等
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _call(self, messages: list[dict], temperature: float = 0.2) -> str:
        """
        调用 LLM 接口，获取模型的响应。

        Args:
            messages (list[dict]): 消息列表，每条消息包含 role 和 content。
            temperature (float): 控制生成文本的随机性。
            max_tokens (int): 最大生成 token 数量。

        Returns:
            str: 模型生成的文本响应。
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception:
            return ""
        
    def _call_with_tools(self, messages: list[dict], tools: list[dict], temperature: float = 0.2) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "tools": tools,
        }

        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]
        except Exception:
            return {"content": None, "tool_calls": None}

    def classify_and_summarize(
        self,
        commit_message: str,
        pr_title: str | None = None,
        pr_description: str | None = None,
    ) -> dict:
        prompt = f"""Analyze this code change and output JSON only.

            Commit message: {commit_message}
            PR title: {pr_title or "N/A"}
            PR description: {pr_description or "N/A"}

            Classify the change type:
            - feature: 新增功能
            - bugfix: 修复 bug
            - refactor: 重构/优化
            - chore: 工程/配置变更
            - docs: 文档变更
            - test: 测试变更

            Then write a one-line Chinese summary of what this change did.

            Output format (JSON only):
            {{"change_type": "feature", "summary": "..."}}
        """
        messages = [
            {"role": "system", "content": "You are a code review assistant. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]

        result = self._call(messages)
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"change_type": "unknown", "summary": commit_message[:50]}
        
    def trace_function_change(
        self,
        function_name: str,
        old_body: str,
        new_file_functions: list[str],
    ) -> dict | None:
        prompt = f"""Function "{function_name}" disappeared after a commit.
The new commit has these functions: {new_file_functions}

Old version of the function:
```python
{old_body[:1500]}
```

Analyze what happened to this function. Output ONLY valid JSON:
{{"action": "renamed|split|merged|deleted|unknown", "new_name": "the new function name or empty string", "note": "one sentence in Chinese explaining what you think happened"}}
"""
        messages = [
            {"role": "system", "content": "You are a code analysis assistant. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]
        result = self._call(messages)
        try:
            return json.loads(result) if result else None
        except json.JSONDecodeError:
            return {"action": "unknown", "new_name": "", "note": "LLM 解析失败"}

    def match_function_across_files(
        self,
        function_name: str,
        old_body: str,
        candidates: list[dict],
    ) -> dict | None:
        """
        函数在某次 commit 后从原文件消失，在多个候选文件/函数中找出哪个
        最有可能是该函数的后继（被重命名/拆分/合并到其他文件）。

        Args:
            function_name: 原函数名。
            old_body: 原函数体。
            candidates: 候选函数列表，每项含 {file, name, body}。

        Returns:
            {"matched": True, "file": "...", "name": "...", "note": "..."}
            或 {"matched": False, "note": "..."}
        """
        if not candidates:
            return {"matched": False, "note": "无可候选函数"}

        # 精简候选：只传 name + body 前 200 字符给 LLM
        candidate_text = "\n---\n".join(
            f"文件: {c['file']}\n函数: {c['name']}\n代码:\n{c.get('body', '')[:200]}"
            for c in candidates[:8]
        )
        prompt = f"""函数 "{function_name}" 在某次提交后从原文件中消失，疑似被移动或改名到其他文件。

旧函数代码:
```python
{old_body[:1500]}
```

其他文件中找到的候选函数:
{candidate_text}

请分析哪个候选函数最有可能是原函数的后继（改名、拆分、合并到其他文件）。如果都不像，返回 matched: false。

只输出 JSON:
{{"matched": true/false, "file": "候选所在文件路径", "name": "候选函数名", "note": "一句话中文说明判断理由"}}
"""
        messages = [
            {"role": "system", "content": "You are a code analysis assistant. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]
        result = self._call(messages)
        try:
            return json.loads(result) if result else {"matched": False, "note": "LLM 返回为空"}
        except json.JSONDecodeError:
            return {"matched": False, "note": "LLM 返回解析失败"}
