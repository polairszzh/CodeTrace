import os
from services.llm_service import LLMService

client = LLMService(
    api_key=os.getenv("LLM_API_KEY", ""),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    model=os.getenv("LLM_MODEL", "deepseek-v4-pro"),
)


def test_classify_and_summarize_feature():
    result = client.classify_and_summarize(
        commit_message="feat: add user login page (#42)",
        pr_title="Add user login page",
        pr_description="Implemented a new login page with email and password authentication",
    )
    assert "change_type" in result
    assert "summary" in result
    assert result["change_type"] == "feature"


def test_classify_and_summarize_without_pr():
    result = client.classify_and_summarize(
        commit_message="fix: resolve timeout issue",
    )
    assert "change_type" in result
    assert "summary" in result
    assert result["change_type"] == "bugfix"
