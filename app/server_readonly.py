from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import publish_api

APP_VERSION = "1.0"
PUBLISHER: publish_api.PublishManager | None = None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def timestamp_order(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def flatten_profile(profile: object) -> list[tuple[str, str, object]]:
    if not isinstance(profile, dict):
        return []
    records: list[tuple[str, str, object]] = [("settings", "default", profile.get("preferences", {}))]
    for record_id, payload in (profile.get("notes") or {}).items():
        records.append(("note", str(record_id), payload))
    for page_key, items in (profile.get("highlights") or {}).items():
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("id"):
                records.append(("highlight", str(item["id"]), {**item, "pageKey": page_key}))
    for book_id, pages in (profile.get("bookmarks") or {}).items():
        for page in pages if isinstance(pages, list) else []:
            records.append(("bookmark", f"{book_id}::{page}", {"bookId": book_id, "page": page}))
    for book_id, page in (profile.get("progress") or {}).items():
        records.append(("progress", str(book_id), {"bookId": book_id, "page": page}))
    for item in profile.get("recent") or []:
        if isinstance(item, dict) and item.get("bookId"):
            records.append(("recent", str(item["bookId"]), item))
    records.append(("statistics", "default", profile.get("statistics", {})))
    records.append(("workspace", "default", profile.get("workspace", {})))
    return records


def init_database(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sync_records (
                kind TEXT NOT NULL, record_id TEXT NOT NULL, payload TEXT, updated_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL,
                device_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(kind, record_id)
            )"""
        )
        connection.execute("CREATE INDEX IF NOT EXISTS sync_records_revision_idx ON sync_records(revision)")
        count = connection.execute("SELECT COUNT(*) FROM sync_records").fetchone()[0]
        legacy = connection.execute("SELECT payload, updated_at FROM profiles WHERE id='default'").fetchone()
        if count == 0 and legacy:
            try:
                for revision, (kind, record_id, payload) in enumerate(flatten_profile(json.loads(legacy[0])), 1):
                    connection.execute(
                        "INSERT INTO sync_records(kind,record_id,payload,updated_at,deleted,revision,device_id) VALUES(?,?,?, ?,0,?,?)",
                        (kind, record_id, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), legacy[1] or utc_timestamp(), revision, "legacy-profile"),
                    )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass


def sync_rows(connection: sqlite3.Connection, since: int) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT kind,record_id,payload,updated_at,deleted,revision,device_id FROM sync_records WHERE revision>? ORDER BY revision",
        (max(0, since),),
    ).fetchall()
    result = []
    for kind, record_id, payload, updated_at, deleted, revision, device_id in rows:
        record: dict[str, object] = {"kind": kind, "id": record_id, "updatedAt": updated_at, "deleted": bool(deleted), "revision": revision, "deviceId": device_id}
        if not deleted:
            try:
                record["payload"] = json.loads(payload) if payload else None
            except json.JSONDecodeError:
                record["payload"] = None
        result.append(record)
    return result


def sync_changes(database: Path, body: dict[str, object]) -> dict[str, object]:
    device_id = str(body.get("deviceId", ""))[:120] or "unknown-device"
    since = max(0, int(body.get("since", 0) or 0))
    changes = body.get("changes", [])
    if not isinstance(changes, list) or len(changes) > 10000:
        raise ValueError("同步记录格式无效")
    with sqlite3.connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        revision = connection.execute("SELECT COALESCE(MAX(revision),0) FROM sync_records").fetchone()[0]
        accepted = 0
        for change in changes:
            if not isinstance(change, dict):
                continue
            kind = str(change.get("kind", ""))[:40]
            record_id = str(change.get("id", ""))[:500]
            updated_at = str(change.get("updatedAt", ""))[:50] or utc_timestamp()
            deleted = 1 if change.get("deleted") else 0
            if not kind or not record_id:
                continue
            existing = connection.execute("SELECT updated_at,device_id FROM sync_records WHERE kind=? AND record_id=?", (kind, record_id)).fetchone()
            if existing and (timestamp_order(updated_at), device_id) <= (timestamp_order(existing[0]), existing[1]):
                continue
            encoded = None if deleted else json.dumps(change.get("payload"), ensure_ascii=False, separators=(",", ":"))
            revision += 1
            accepted += 1
            connection.execute(
                """INSERT INTO sync_records(kind,record_id,payload,updated_at,deleted,revision,device_id)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(kind,record_id) DO UPDATE SET
                   payload=excluded.payload,updated_at=excluded.updated_at,deleted=excluded.deleted,
                   revision=excluded.revision,device_id=excluded.device_id""",
                (kind, record_id, encoded, updated_at, deleted, revision, device_id),
            )
        records = sync_rows(connection, since)
        server_revision = connection.execute("SELECT COALESCE(MAX(revision),0) FROM sync_records").fetchone()[0]
    return {"ok": True, "accepted": accepted, "serverRevision": server_revision, "records": records}


class ReaderHandler(SimpleHTTPRequestHandler):
    server_version = "BookBotRemote/0.2"

    def __init__(self, *args: object, directory: str, data_dir: str, **kwargs: object) -> None:
        self.library_root = Path(directory).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.database = self.data_dir / "reader.db"
        super().__init__(*args, directory=str(self.library_root), **kwargs)

    def publisher(self) -> publish_api.PublishManager:
        if PUBLISHER is None:
            raise RuntimeError("发布模块未初始化")
        return PUBLISHER

    def require_publish_auth(self) -> bool:
        try:
            if not self.publisher().auth_ok(self.headers.get("Authorization", "")):
                self.send_json({"ok": False, "error": "发布令牌无效"}, 401)
                return False
            return True
        except publish_api.PublishError as error:
            self.send_json({"ok": False, "error": str(error)}, error.status)
            return False

    def authenticated(self) -> bool:
        expected = f"{os.environ.get('READER_USERNAME', 'reader')}:{os.environ.get('READER_PASSWORD', '')}".encode()
        supplied = self.headers.get("Authorization", "")
        return bool(expected.split(b":", 1)[1]) and supplied == "Basic " + base64.b64encode(expected).decode()

    def require_auth(self) -> bool:
        if self.authenticated():
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="BookBot Private Library", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if route.startswith("/api/publish/"):
            if not self.require_publish_auth():
                return
            try:
                if route == "/api/publish/library":
                    self.send_json(self.publisher().library_snapshot())
                    return
                if route == "/api/publish/status":
                    session = urllib.parse.parse_qs(parsed.query).get("session", [""])[0]
                    self.send_json(self.publisher().status(session))
                    return
            except publish_api.PublishError as error:
                self.send_json({"ok": False, "error": str(error)}, error.status)
                return
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, 500)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self.require_auth():
            return
        if route == "/api/local-library":
            self.send_json({"books": [], "desktopMode": False})
            return
        if route == "/api/version":
            self.send_json({"version": APP_VERSION, "mode": "remote"})
            return
        if route == "/api/status":
            disk = shutil.disk_usage(self.library_root)
            self.send_json({"status": "idle", "message": "服务器书库已连接", "available": True, "disk": {"free": disk.free, "total": disk.total}})
            return
        if route == "/api/library-info":
            library_file = self.library_root / "assets" / "library.json"
            try:
                library = json.loads(library_file.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                library = []
            entries = library if isinstance(library, list) else []
            result = []
            for item in entries:
                asset_base = str(item.get("assetBase", ""))
                legacy = asset_base == "assets"
                shared = sum(1 for other in entries if str(other.get("assetBase", "")) == asset_base) > 1
                folder = (self.library_root / asset_base).resolve()
                root = self.library_root.resolve()
                size = sum(path.stat().st_size for path in folder.rglob("*") if path.is_file()) if (folder == root or root in folder.parents) and folder.is_dir() else 0
                result.append({"id": item.get("id"), "outputBytes": size, "removable": False, "legacy": legacy, "shared": shared})
            self.send_json({"books": result})
            return
        if route == "/api/profile":
            with sqlite3.connect(self.database) as connection:
                row = connection.execute("SELECT payload, updated_at FROM profiles WHERE id='default'").fetchone()
            self.send_json({"profile": {"data": json.loads(row[0]), "updatedAt": row[1]} if row else None})
            return
        if route == "/api/sync":
            try:
                since = max(0, int(urllib.parse.parse_qs(parsed.query).get("since", ["0"])[0]))
                with sqlite3.connect(self.database) as connection:
                    records = sync_rows(connection, since)
                    revision = connection.execute("SELECT COALESCE(MAX(revision),0) FROM sync_records").fetchone()[0]
                self.send_json({"ok": True, "serverRevision": revision, "records": records})
            except ValueError as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            return
        if route.startswith("/.publish") or route.startswith("/.previous"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        super().do_GET()

    def do_PUT(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/publish/file":
            if not self.require_publish_auth():
                return
            try:
                query = urllib.parse.parse_qs(parsed.query)
                session = query.get("session", [""])[0]
                relative = query.get("path", [""])[0]
                offset = int(query.get("offset", ["0"])[0])
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > publish_api.MAX_FILE_BYTES:
                    raise publish_api.PublishError("分块大小无效")
                data = self.rfile.read(length) if length else b""
                self.send_json(self.publisher().write_chunk(session, relative, offset, data))
            except (publish_api.PublishError, ValueError) as error:
                self.send_json({"ok": False, "error": str(error)}, getattr(error, "status", 400))
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, 500)
            return
        if not self.require_auth():
            return
        if parsed.path != "/api/profile":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4 * 1024 * 1024:
                raise ValueError("阅读数据大小无效")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("阅读数据格式无效")
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            with sqlite3.connect(self.database) as connection:
                connection.execute("INSERT INTO profiles(id,payload,updated_at) VALUES('default',?,datetime('now')) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (encoded,))
            self.send_json({"ok": True})
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.send_json({"ok": False, "error": str(error)}, 400)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if route in ("/api/publish/plan", "/api/publish/commit"):
            if not self.require_publish_auth():
                return
            try:
                if route == "/api/publish/plan":
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 8 * 1024 * 1024:
                        raise publish_api.PublishError("请求大小无效")
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    self.send_json(self.publisher().plan(body))
                else:
                    session = urllib.parse.parse_qs(parsed.query).get("session", [""])[0]
                    self.send_json(self.publisher().commit(session))
            except (publish_api.PublishError, ValueError, UnicodeDecodeError) as error:
                self.send_json({"ok": False, "error": str(error)}, getattr(error, "status", 400))
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, 500)
            return
        if not self.require_auth():
            return
        if route == "/api/sync":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 8 * 1024 * 1024:
                    raise ValueError("同步数据大小无效")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("同步数据格式无效")
                self.send_json(sync_changes(self.database, payload))
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            return
        self.send_json({"ok": False, "error": "服务器版只负责阅读；导入和删除请在电脑端完成"}, 403)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3011)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    data_dir = Path(args.data)
    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "reader.db"
    init_database(database)
    global PUBLISHER
    PUBLISHER = publish_api.PublishManager(
        library_root=Path(args.directory),
        data_dir=data_dir,
        token=os.environ.get("BOOKBOT_PUBLISH_TOKEN", ""),
    )
    if not os.environ.get("READER_PASSWORD"):
        raise SystemExit("READER_PASSWORD 未设置，拒绝启动公网私人书库")
    factory = lambda *handler_args, **handler_kwargs: ReaderHandler(*handler_args, directory=args.directory, data_dir=args.data, **handler_kwargs)
    server = ThreadingHTTPServer((args.host, args.port), factory)
    print(f"BookBot remote reader: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
