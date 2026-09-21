import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, field_validator


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
DB_PATH = ROOT_DIR / "app.db"
SEED_PATH = ROOT_DIR / "seed.json"


def error_payload(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}


def ok_payload(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data}


def ensure_db_ready() -> None:
    init_db()
    seed_db_if_empty()


def get_db() -> sqlite3.Connection:
    ensure_db_ready()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            source_name TEXT NOT NULL,
            published_at TEXT NOT NULL,
            url TEXT NOT NULL,
            summary TEXT,
            tags TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def seed_db_if_empty() -> None:
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if count > 0:
        conn.close()
        return

    with SEED_PATH.open("r", encoding="utf-8") as fh:
        seed = json.load(fh)

    for item in seed.get("items", []):
        source_name = item.get("source", {}).get("name")
        conn.execute(
            """
            INSERT INTO items (title, source_name, published_at, url, summary, tags)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("title"),
                source_name,
                item.get("publishedAt"),
                item.get("url"),
                item.get("summary"),
                json.dumps(item.get("tags", [])),
            ),
        )

    conn.commit()
    conn.close()


def row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "source": {"name": row["source_name"]},
        "publishedAt": row["published_at"],
        "url": row["url"],
        "summary": row["summary"],
        "tags": json.loads(row["tags"] or "[]"),
    }


app = FastAPI(title="CMPUT 401 Assignment API")


@app.on_event("startup")
def startup_event() -> None:
    ensure_db_ready()


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content=error_payload("VALIDATION_ERROR", "Request validation failed"),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "status" in detail and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload("HTTP_ERROR", str(detail)),
    )


class ItemCreateRequest(BaseModel):
    title: str
    source: dict
    publishedAt: str
    url: str
    summary: str | None = None
    tags: list[str] | None = None

    @field_validator("title", "url", "publishedAt")
    @classmethod
    def check_required_strings(cls, value: str) -> str:
        if value is None or str(value).strip() == "":
            raise ValueError("This field is required")
        return value

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: dict) -> dict:
        if not isinstance(value, dict) or not value.get("name") or not str(value.get("name")).strip():
            raise ValueError("source.name is required")
        return value

    @field_validator("publishedAt")
    @classmethod
    def validate_published_at(cls, value: str) -> str:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("publishedAt must be a valid ISO 8601 timestamp") from exc

        if dt.tzinfo is None:
            raise ValueError("publishedAt must include timezone information")
        return value


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return """
    <html>
      <head><title>Assignment Frontend</title></head>
      <body>
        <div data-testid="app-root">
          <button data-testid="load-button" onclick="loadItems()">Load</button>
          <div>Count: <span data-testid="items-count">0</span></div>
          <div>First item: <span data-testid="first-item-title">No items</span></div>
        </div>
        <script>
          async function loadItems() {
            const response = await fetch('/api/v1/items');
            const payload = await response.json();
            const items = payload.data || [];
            const countEl = document.querySelector('[data-testid="items-count"]');
            const titleEl = document.querySelector('[data-testid="first-item-title"]');
            countEl.textContent = items.length;
            titleEl.textContent = items.length ? items[0].title : 'No items';
          }
        </script>
      </body>
    </html>
    """


@app.get("/api/v1/items")
def get_items(limit: int = Query(10, ge=1, le=50), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM items ORDER BY id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    conn.close()
    return ok_payload([row_to_item(row) for row in rows])


@app.get("/api/v1/items/{item_id}")
def get_item(item_id: int) -> dict[str, Any]:
    conn = get_db()
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail=error_payload("NOT_FOUND", "Item not found"))

    return ok_payload(row_to_item(row))


@app.post("/api/v1/items", status_code=201)
def create_item(payload: ItemCreateRequest) -> dict[str, Any]:
    if not payload.title or not str(payload.title).strip():
        raise HTTPException(status_code=400, detail=error_payload("VALIDATION_ERROR", "title is required"))
    if not payload.source or not payload.source.get("name") or not str(payload.source.get("name")).strip():
        raise HTTPException(status_code=400, detail=error_payload("VALIDATION_ERROR", "source.name is required"))
    if not payload.url or not str(payload.url).strip():
        raise HTTPException(status_code=400, detail=error_payload("VALIDATION_ERROR", "url is required"))

    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO items (title, source_name, published_at, url, summary, tags)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.title,
                payload.source["name"],
                payload.publishedAt,
                payload.url,
                payload.summary,
                json.dumps(payload.tags or []),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM items WHERE id = ?", (cursor.lastrowid,)).fetchone()
        conn.close()
        return ok_payload(row_to_item(row))
    except Exception:
        conn.close()
        raise HTTPException(status_code=400, detail=error_payload("VALIDATION_ERROR", "Unable to create item"))


@app.patch("/api/v1/items/{item_id}")
def patch_item(item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    if "id" in payload:
        raise HTTPException(status_code=400, detail=error_payload("VALIDATION_ERROR", "id cannot be changed"))

    conn = get_db()
    existing = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if existing is None:
        conn.close()
        raise HTTPException(status_code=404, detail=error_payload("NOT_FOUND", "Item not found"))

    updates: list[str] = []
    values: list[Any] = []

    if "title" in payload:
        updates.append("title = ?")
        values.append(str(payload["title"]).strip())
    if "source" in payload and isinstance(payload["source"], dict) and payload["source"].get("name"):
        updates.append("source_name = ?")
        values.append(str(payload["source"]["name"]).strip())
    if "publishedAt" in payload:
        updates.append("published_at = ?")
        values.append(str(payload["publishedAt"]))
    if "url" in payload:
        updates.append("url = ?")
        values.append(str(payload["url"]).strip())
    if "summary" in payload:
        updates.append("summary = ?")
        values.append(payload["summary"])
    if "tags" in payload:
        updates.append("tags = ?")
        values.append(json.dumps(payload["tags"]))

    if not updates:
        conn.close()
        return ok_payload({"id": item_id})

    values.append(item_id)
    conn.execute(f"UPDATE items SET {', '.join(updates)} WHERE id = ?", tuple(values))
    conn.commit()
    updated = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return ok_payload({"id": item_id, "item": row_to_item(updated)})


@app.delete("/api/v1/items/{item_id}", status_code=204)
def delete_item(item_id: int) -> None:
    conn = get_db()
    row = conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail=error_payload("NOT_FOUND", "Item not found"))

    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)
