import json
import logging

import httpx

logger = logging.getLogger(__name__)


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
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("LLM _call 失败: %s | model=%s", e, self.model)
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
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]
        except Exception as e:
            logger.warning("LLM _call_with_tools 失败: %s | model=%s", e, self.model)
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

    def classify_file_health(self, file_stats: list[dict]) -> list[dict]:
        """
        对健康度检测结果做批量语义分类—识别哪些 commit 是 bug 修复、功能新增、重构等。
        代替旧版关键词匹配（fix/bug 等）。

        Args:
            file_stats: get_file_health_stats 的输出，每项含 file, total_commits, commit_messages。

        Returns:
            每项追加 semantic_bug_probability (0-1) 和 notable_pattern 的列表。
        """
        if not file_stats:
            return file_stats

        # 构造批次：传文件路径 + commit messages 给 LLM
        batch_text = "\n\n".join(
            f"[{i}] 文件: {s['file']} (共 {s['total_commits']} 次提交)\n"
            f"    最近 commit: {'; '.join(s.get('commit_messages', [])[:5])}"
            for i, s in enumerate(file_stats)
        )

        prompt = f"""分析以下每个文件的 commit 信息，输出 JSON 数组。
对每个文件判断：

1. bug_probability (0-1): 这些 commit 中多少比例看起来是 bug 修复？0 = 完全不是，1 = 全是修 bug
2. risk_level (low/medium/high): 综合 commit 内容和变更频率，这个文件的风险级别
3. notable_pattern: 一句话说明值得关注的点（中文，20 字内），如果没有则空字符串

只输出 JSON 数组，不要其他文字：

[
  {{"index": 0, "bug_probability": 0.3, "risk_level": "low", "notable_pattern": "主要为功能新增"}},
  ...
]

文件数据:
{batch_text}
"""
        messages = [
            {"role": "system", "content": "You are a code repository analyst. Output only valid JSON arrays."},
            {"role": "user", "content": prompt},
        ]
        result = self._call(messages)
        try:
            import json as _json
            parsed = _json.loads(result) if result else []
        except (_json.JSONDecodeError, TypeError):
            parsed = []

        parsed_by_index = {p.get("index"): p for p in parsed if isinstance(p, dict)}
        updated_stats = []
        for i, s in enumerate(file_stats):
            annotation = parsed_by_index.get(i, {})
            s["semantic_bug_probability"] = annotation.get("bug_probability", 0.5)
            s["risk_level"] = annotation.get("risk_level", "medium")
            s["notable_pattern"] = annotation.get("notable_pattern", "")
            updated_stats.append(s)
        return updated_stats


    def detect_refactor_events(self, commit_groups: list[dict]) -> list[dict]:
        """
        检测跨文件重构事件：传入最近 N 个 commit 的变更组信息，
        LLM 判断哪些 commit 是跨文件重构，返回合并后的事件列表。

        Args:
            commit_groups: get_recent_commit_groups 的输出。

        Returns:
            list[dict]: 每项一个事件 {refactor_type, summary, commits: [...]}
        """
        if not commit_groups:
            return []

        # 简化每个 commit 数据（去掉详细文件列表，只保留路径名和 churn）
        simplified = []
        for cg in commit_groups:
            files_short = [f"{f['path']}(+{f['additions']}/-{f['deletions']})" for f in cg.get("files", [])[:8]]
            simplified.append({
                "hash": cg["commit_hash"][:8],
                "message": cg["message"],
                "files": cg.get("files", []),
                "file_count": cg["file_count"],
                "total_churn": cg["total_churn"],
                "file_paths": files_short,
            })

        import json as _json
        batch = _json.dumps(simplified, ensure_ascii=False)

        prompt = f"""分析以下最近 commit 数据，判断其中的跨文件重构事件。

一条 commit 如果同时满足以下条件，很可能是一次重构：

1. 修改了 3 个以上文件
2. 文件的修改有关联（如同属一个模块/功能），并非无关文件的随机集合
3. commit message 描述一个统一的行为（如"重构认证模块""迁移数据库层"）

对于符合条件的事件，请合并为一条记录。相邻多个 commit 如果属于同一个持续重构过程，也应合并。

输出 JSON 数组：
[{{"refactor_type": "rename|split|extract|migrate|restructure|unknown",
   "summary": "一句话中文重构描述",
   "file_count": 涉及的文件数,
   "files": ["file1.py", "file2.py"],
   "commits": ["hash1短值", ...],
   "confidence": "high|medium|low"
}}]

如果没有任何重构事件，输出 []。

Commit 数据：
{batch}
"""
        messages = [
            {"role": "system", "content": "You are a code repository analyst. Detect cross-file refactoring events. Output only valid JSON arrays."},
            {"role": "user", "content": prompt},
        ]
        result = self._call(messages)
        try:
            parsed = _json.loads(result) if result else []
        except (_json.JSONDecodeError, TypeError):
            parsed = []
        return parsed if isinstance(parsed, list) else []

    def analyze_coupling_trends(self, trends: list[dict]) -> list[dict]:
        """
        对双窗口 co-change 趋势数据做语义分析，输出每个文件的风险解读。

        Args:
            trends: get_co_change_trends 的输出。

        Returns:
            追加 warning 和 suggestion 后的列表。
        """
        if not trends:
            return trends

        batch_text = "\n\n".join(
            f"[{i}] {t['file']}: 伙伴数 {t['old_partners']}→{t['recent_partners']} "
            f"(增长 {t.get('coupling_growth', 0)}), "
            f"跨模块共现 {t.get('boundary_crossings', 0)} 次, "
            f"当前风险: {t.get('risk', 'medium')}"
            for i, t in enumerate(trends[:20])
        )

        prompt = f"""分析以下每个文件的耦合趋势数据。

coupling_growth > 0.3 且 recent_partners > 3 说明耦合面显著扩大。
boundary_crossings 高说明频繁跨模块耦合，可能是架构侵蚀的信号。

对每条数据输出 JSON 数组：
[{{"index": 0, "warning": "一句话中文风险描述（30字内）", "suggestion": "一句话改进建议（30字内）"}}]

数据:
{batch_text}
"""
        import json as _json
        messages = [
            {"role": "system", "content": "You are a software architecture analyst. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]
        result = self._call(messages)
        try:
            parsed = _json.loads(result) if result else []
        except (_json.JSONDecodeError, TypeError):
            parsed = []

        by_idx = {p.get("index"): p for p in parsed if isinstance(p, dict)}
        for i, t in enumerate(trends):
            ann = by_idx.get(i, {})
            t["warning"] = ann.get("warning", "")
            t["suggestion"] = ann.get("suggestion", "")
        return trends

    def explain_change_reason(self, commit_contexts: list[dict]) -> list[dict]:
        """
        对文件最近 N 个 commit 的变更上下文做原因理解。
        综合 message + diff，输出每个 commit 的"为什么改"。

        Args:
            commit_contexts: get_file_change_context 的输出。

        Returns:
            追加 reason, why, effort 后的列表。
        """
        if not commit_contexts:
            return commit_contexts

        batch_items = []
        for ctx in commit_contexts:
            safe_diff = ctx.get("diff_summary", "")
            if len(safe_diff) > 800:
                safe_diff = safe_diff[:800] + "\n...(truncated)"
            diff_html = safe_diff.replace("{", "{{").replace("}", "}}")
            batch_items.append(f"[{commit_contexts.index(ctx)}] {ctx['message']}\n变更:\n{diff_html}")

        batch_text = "\n---\n".join(batch_items)

        prompt = f"""分析以下某文件最近几次 commit 的变更信息，理解每次变更背后的原因。

对每个 commit 输出 JSON 数组：
[{{"index": 0, "reason": "变更的核心目的（一句话中文，20字内）", "why": "具体的业务或技术原因（一句话中文，40字内）", "effort": "small|medium|large"}}]

- reason: 变更的核心目的，如"修复空指针""重构认证逻辑""添加分页参数"
- why: 变更原因，如"用户反馈登录闪退""模块耦合过高需要解耦""API 响应太慢需要缓存"
- effort: 根据 diff 行数评估工作量大小

数据:
{batch_text}
"""
        import json as _json
        messages = [
            {"role": "system", "content": "You are a code historian who understands why changes were made. Output only valid JSON arrays."},
            {"role": "user", "content": prompt},
        ]
        result = self._call(messages)
        try:
            parsed = _json.loads(result) if result else []
        except (_json.JSONDecodeError, TypeError):
            parsed = []

        by_idx = {p.get("index"): p for p in parsed if isinstance(p, dict)}
        for i, c in enumerate(commit_contexts):
            ann = by_idx.get(i, {})
            c["reason"] = ann.get("reason", "")
            c["why"] = ann.get("why", "")
            c["effort"] = ann.get("effort", "medium")
        return commit_contexts
