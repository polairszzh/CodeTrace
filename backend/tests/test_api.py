from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_trace_endpoint():
    response = client.post("/api/trace", json={
        "repo_url": "https://github.com/polairszzh/CodeTrace.git",
        "file_path": ".gitignore"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["repo"] == "polairszzh/CodeTrace"
    assert data["file_path"] == ".gitignore"
    assert len(data["timeline"]) > 0
    assert data["timeline"][0]["commit_hash"]
    assert data["timeline"][0]["change_type"]
    assert data["timeline"][0]["summary"]


def test_trace_function_endpoint():
    response = client.post("/api/trace/function?function_name=get_file_commits", json={
        "repo_url": "https://github.com/polairszzh/CodeTrace.git",
        "file_path": "backend/services/git_stats.py"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["function_name"] == "get_file_commits"
    assert len(data["history"]) > 0
    assert data["history"][0]["function"]["name"] == "get_file_commits"


def test_trace_function_not_found():
    response = client.post("/api/trace/function?function_name=non_existent_func", json={
        "repo_url": "https://github.com/polairszzh/CodeTrace.git",
        "file_path": "backend/services/git_stats.py"
    })
    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 0
