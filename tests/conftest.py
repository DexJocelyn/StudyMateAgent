import pytest
from backend.storage.db import initialize


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('STUDYMATE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('AGENT_MODE', 'demo')
    monkeypatch.delenv('MODEL_API_KEY', raising=False)
    monkeypatch.setenv('TAVILY_API_KEY', '')
    monkeypatch.setenv('WEB_SEARCH_ENABLED', '1')
    monkeypatch.setenv('WEATHER_ENABLED', '1')
    monkeypatch.setenv('WEB_SEARCH_MONTHLY_LIMIT', '100')
    initialize()
    return tmp_path


@pytest.fixture
def task_data():
    return {'title': '接口联调', 'assignee_id': 'member_a', 'deadline': '2030-10-25T22:00:00+08:00',
            'estimated_minutes': 60}


@pytest.fixture
def client(isolated):
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as client:
        yield client
