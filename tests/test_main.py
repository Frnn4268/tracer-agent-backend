from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_health_endpoint_returns_ok():
    response = client.get('/health')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_root_reports_api_metadata():
    response = client.get('/')

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'ok'
    assert payload['chat_endpoint'] == '/api/chat/'
    assert isinstance(payload['allowed_origins'], list)