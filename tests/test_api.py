import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_seed_items_loaded_on_startup():
    response = client.get("/api/v1/items")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert len(payload["data"]) == 10
    assert payload["data"][0]["title"]


def test_create_item_validates_required_fields():
    bad = {"title": "x", "source": {"name": "test"}}
    response = client.post("/api/v1/items", json=bad)
    assert response.status_code == 400
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "VALIDATION_ERROR"


def test_create_item_success():
    payload = {
        "title": "Test item",
        "source": {"name": "Test Source"},
        "publishedAt": "2025-03-03T12:00:00Z",
        "url": "https://example.com/test-item",
        "summary": "Example summary",
        "tags": ["test", "sample"],
    }
    response = client.post("/api/v1/items", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ok"
    assert body["data"]["title"] == "Test item"
    assert "id" in body["data"]


def test_patch_rejects_id_in_body():
    response = client.patch("/api/v1/items/1", json={"id": 7, "title": "bad"})
    assert response.status_code == 400
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "VALIDATION_ERROR"


def test_delete_item():
    create_response = client.post(
        "/api/v1/items",
        json={
            "title": "To delete",
            "source": {"name": "Delete Source"},
            "publishedAt": "2025-04-02T00:00:00Z",
            "url": "https://example.com/delete-me",
            "summary": "delete me",
            "tags": ["delete"],
        },
    )
    item_id = create_response.json()["data"]["id"]

    response = client.delete(f"/api/v1/items/{item_id}")
    assert response.status_code == 204
    assert response.content == b""
