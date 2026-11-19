from tests.api_client import request


def test_health_endpoint() -> None:
    response = request("GET", "/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

