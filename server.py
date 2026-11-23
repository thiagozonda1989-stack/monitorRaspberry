"""API e painel WSGI com persistência SQLite."""
from contextlib import closing
from datetime import datetime, timedelta
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs
from uuid import UUID

from monitor import BATCH, DEVICE_PATTERN, FIELDS, database, utc_now

WEB = Path(__file__).resolve().parent / "web"
ASSETS = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/style.css": ("style.css", "text/css")}
RANGES = ((-55, 125), (-55, 125), (0, 30), (0, 300))


def authorized(environ, secret):
    supplied = environ.get("HTTP_AUTHORIZATION", "").encode()
    return len(secret) >= 24 and hmac.compare_digest(supplied, ("Bearer " + secret).encode())


def validate(row):
    if not isinstance(row, dict) or set(row) != {"id", "device", "measured_at", "demo",
                                                   "errors", *FIELDS}:
        raise ValueError("Campos inválidos")
    if str(UUID(row["id"])) != row["id"] or not re.fullmatch(DEVICE_PATTERN, row["device"]):
        raise ValueError("ID inválido")
    measured = datetime.fromisoformat(row["measured_at"])
    if measured.utcoffset() != timedelta(0):
        raise ValueError("Use UTC")
    row["measured_at"] = measured.isoformat(timespec="milliseconds")
    errors = row["errors"]
    if not isinstance(row["demo"], bool) or not isinstance(errors, dict):
        raise ValueError("Status inválido")
    if set(errors) != {key for key in FIELDS if row[key] is None}:
        raise ValueError("Status dos sensores inconsistente")
    if any(not isinstance(value, str) or len(value) > 100 for value in errors.values()):
        raise ValueError("Falha inválida")
    for key, (low, high) in zip(FIELDS, RANGES):
        value = row[key]
        if value is not None and (type(value) not in (float, int) or
                                  not math.isfinite(value) or not low <= value <= high):
            raise ValueError("Leitura fora da faixa")
    return row


def server_db():
    db = database(os.getenv("SERVER_DB", "server.sqlite3"))
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS readings (
            id TEXT PRIMARY KEY, device TEXT NOT NULL, measured_at TEXT NOT NULL,
            received_at TEXT NOT NULL, payload TEXT NOT NULL)""")
        db.execute("CREATE INDEX IF NOT EXISTS history ON readings(device, measured_at)")
        db.execute("CREATE INDEX IF NOT EXISTS contact ON readings(device, received_at)")
    return db


def ingest(environ):
    length = int(environ.get("CONTENT_LENGTH") or "0")
    if not 0 < length <= 131072:
        return "413 Payload Too Large", {"error": "tamanho de lote inválido"}
    rows = json.loads(environ["wsgi.input"].read(length))
    if not isinstance(rows, list) or not 1 <= len(rows) <= BATCH:
        raise ValueError("Lote inválido")
    rows = [validate(row) for row in rows]
    received = utc_now()
    with closing(server_db()) as db, db:
        for row in rows:
            payload = json.dumps(row, sort_keys=True)
            result = db.execute("INSERT INTO readings VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                                (row["id"], row["device"], row["measured_at"], received, payload))
            if not result.rowcount:
                previous, = db.execute("SELECT payload FROM readings WHERE id=?",
                                       (row["id"],)).fetchone()
                if previous != payload:
                    raise ValueError("ID já usado para outra leitura")
    return "200 OK", {"accepted": [row["id"] for row in rows]}


def history(environ):
    query = parse_qs(environ.get("QUERY_STRING", ""))
    limit = int(query.get("limit", ["480"])[0])
    if not 1 <= limit <= 3000:
        raise ValueError("Limite inválido")
    with closing(server_db()) as db:
        devices = [row[0] for row in db.execute("SELECT DISTINCT device FROM readings ORDER BY device")]
        device = query.get("device", [devices[0] if devices else ""])[0]
        data = db.execute("""SELECT payload FROM readings WHERE device=?
                             ORDER BY measured_at DESC, id DESC LIMIT ?""", (device, limit)).fetchall()
        received, = db.execute("SELECT max(received_at) FROM readings WHERE device=?", (device,)).fetchone()
    return "200 OK", dict(devices=devices, device=device, last_received_at=received,
                          readings=[json.loads(row[0]) for row in reversed(data)])


def application(environ, start_response):
    path = environ["PATH_INFO"]
    method = environ["REQUEST_METHOD"]
    content_type = "application/json"
    status = "200 OK"
    if path in ASSETS and method == "GET":
        filename, content_type = ASSETS[path]
        body = (WEB / filename).read_bytes()
    else:
        try:
            if path == "/healthz" and method == "GET":
                with closing(server_db()) as db:
                    db.execute("SELECT 1 FROM readings LIMIT 1").fetchone()
                result = {"ok": True}
            elif (path, method) in {("/api/history", "GET"), ("/api/ingest", "POST")}:
                token_name = "READ_TOKEN" if method == "GET" else "UPLOAD_TOKEN"
                if authorized(environ, os.getenv(token_name, "")):
                    status, result = history(environ) if method == "GET" else ingest(environ)
                else:
                    status, result = "401 Unauthorized", {"error": "token inválido"}
            else:
                status, result = "404 Not Found", {"error": "rota inexistente"}
        except (ValueError, TypeError, OverflowError, AttributeError):
            status, result = "400 Bad Request", {"error": "dados inválidos"}
        except sqlite3.Error:
            status, result = "503 Service Unavailable", {"error": "banco indisponível"}
        body = json.dumps(result).encode()
    start_response(status, [("Content-Type", content_type + "; charset=utf-8"),
                            ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                            ("X-Content-Type-Options", "nosniff"),
                            ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")])
    return [body]


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    with make_server("127.0.0.1", 8080, application) as server:
        server.serve_forever()
