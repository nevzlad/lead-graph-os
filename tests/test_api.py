from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["mode"] == "commercial"


def test_internal_analytics_not_registered_in_commercial():
    response = client.get(
        "/internal/analytics",
        params={"tenant_id": "00000000-0000-0000-0000-000000000001"},
        headers={"X-Internal-Token": "fake"},
    )
    assert response.status_code == 404


def test_tenant_stats_query_filters_by_tenant_id():
    import inspect

    from api.routes import public

    source = inspect.getsource(public.tenant_stats)
    assert "tenant_id" in source
    assert "Post.tenant_id == tenant_id" in source
