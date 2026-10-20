from __future__ import annotations

import argparse
import html
import json
import os
import queue
import re
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
INBOX = ROOT / "待处理书籍"
TOOLS = ROOT / "tools"
DATA = ROOT / "data"
PROFILE_DB = DATA / "reader.db"
LOCAL_BOOKS_FILE = DATA / "local-books.json"
PUBLISH_CONFIG_FILE = DATA / "publish-config.json"
PUBLISHED_FILE = DATA / "published.json"
sys.path.insert(0, str(TOOLS))
import book_bot  # noqa: E402
import publish_manifest  # noqa: E402

PUBLISH_DEFAULT_CONFIG = {
    "serverBase": "http://127.0.0.1:3011",
    "publishToken": "",
    "chunkBytes": 4 * 1024 * 1024,
}
CLOUD_LOCK = threading.Lock()
CLOUD_STATE: dict[str, object] = {
    "state": "idle",
    "bookId": "",
    "percent": 0,
    "sentBytes": 0,
    "totalBytes": 0,
    "message": "",
    "error": "",
    "freeBytes": 0,
}

LIBRARY_LOCK = threading.Lock()
PROCESS_QUEUE: queue.Queue[Path] = queue.Queue()
DESKTOP_MODE = False
APP_VERSION = "1.0"


def local_books() -> list[dict[str, object]]:
    result = book_bot.load_json(LOCAL_BOOKS_FILE, [])
    if not isinstance(result, list):
        return []
    available = []
    changed = False
    for item in result:
        source = Path(str(item.get("sourcePath", "")))
        if source.is_file():
            available.append(item)
        else:
            changed = True
    if changed:
        book_bot.save_json(LOCAL_BOOKS_FILE, available)
    return available


def local_book(book_id: str) -> dict[str, object] | None:
    return next((item for item in local_books() if item.get("id") == book_id), None)


def unregister_local_book(book_id: str) -> bool:
    result = book_bot.load_json(LOCAL_BOOKS_FILE, [])
    if not isinstance(result, list):
        return False
    updated = [item for item in result if item.get("id") != book_id]
    if len(updated) == len(result):
        return False
    book_bot.save_json(LOCAL_BOOKS_FILE, updated)
    return True


def forget_book_index(book_id: str) -> None:
    state = book_bot.load_json(book_bot.INDEX_FILE, {"completed": {}})
    if not isinstance(state, dict):
        return
    completed = state.get("completed", {})
    if not isinstance(completed, dict):
        return
    updated = {key: value for key, value in completed.items() if not isinstance(value, dict) or value.get("bookId") != book_id}
    if len(updated) == len(completed):
        return
    state["completed"] = updated
    book_bot.save_json(book_bot.INDEX_FILE, state)


def register_local_pdf(pdf: Path) -> dict[str, object]:
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise ValueError("请选择有效的 PDF 文件")
    reader = book_bot.PdfReader(pdf)
    mark = book_bot.fingerprint(pdf)
    book_id = f"book-{mark[:10]}"
    outline = book_bot.flatten_outline(reader)
    for item in outline:
        item["title"] = html.unescape(str(item.get("title", "")))
    metadata = {
        "id": book_id,
        "title": pdf.stem.strip(),
        "sourceName": pdf.name,
        "sourceBytes": pdf.stat().st_size,
        "pageCount": len(reader.pages),
        "outline": outline,
        "localDirect": True,
        "pdfUrl": f"api/local-books/{book_id}/source",
        "assetBase": f"api/local-books/{book_id}",
        "cover": "assets/local-pdf-cover.svg",
        "sourcePath": str(pdf.resolve()),
    }
    entries = [item for item in local_books() if item.get("id") != book_id]
    entries.append(metadata)
    book_bot.save_json(LOCAL_BOOKS_FILE, entries)
    return metadata


def init_profile_db() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(PROFILE_DB) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sync_records (
                kind TEXT NOT NULL,
                record_id TEXT NOT NULL,
                payload TEXT,
                updated_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL,
                device_id TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(kind, record_id)
            )"""
        )
        connection.execute("CREATE INDEX IF NOT EXISTS sync_records_revision_idx ON sync_records(revision)")
        count = connection.execute("SELECT COUNT(*) FROM sync_records").fetchone()[0]
        legacy = connection.execute("SELECT payload, updated_at FROM profiles WHERE id = ?", ("default",)).fetchone()
        if count == 0 and legacy:
            try:
                records = flatten_profile(json.loads(legacy[0]))
                timestamp = legacy[1] or utc_timestamp()
                for revision, (kind, record_id, payload) in enumerate(records, 1):
                    connection.execute(
                        "INSERT INTO sync_records(kind, record_id, payload, updated_at, deleted, revision, device_id) VALUES(?, ?, ?, ?, 0, ?, ?)",
                        (kind, record_id, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), timestamp, revision, "legacy-profile"),
                    )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass


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
    records: list[tuple[str, str, object]] = []
    records.append(("settings", "default", profile.get("preferences", {})))
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


def sync_rows(connection: sqlite3.Connection, since: int) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT kind, record_id, payload, updated_at, deleted, revision, device_id FROM sync_records WHERE revision > ? ORDER BY revision",
        (max(0, since),),
    ).fetchall()
    result = []
    for kind, record_id, payload, updated_at, deleted, revision, device_id in rows:
        record: dict[str, object] = {
            "kind": kind, "id": record_id, "updatedAt": updated_at, "deleted": bool(deleted),
            "revision": revision, "deviceId": device_id,
        }
        if not deleted:
            try:
                record["payload"] = json.loads(payload) if payload else None
            except json.JSONDecodeError:
                record["payload"] = None
        result.append(record)
    return result


def sync_changes(payload: dict[str, object]) -> dict[str, object]:
    device_id = str(payload.get("deviceId", ""))[:120] or "unknown-device"
    since = max(0, int(payload.get("since", 0) or 0))
    changes = payload.get("changes", [])
    if not isinstance(changes, list) or len(changes) > 10000:
        raise ValueError("同步记录格式无效")
    with sqlite3.connect(PROFILE_DB) as connection:
        connection.execute("BEGIN IMMEDIATE")
        revision = connection.execute("SELECT COALESCE(MAX(revision), 0) FROM sync_records").fetchone()[0]
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
            existing = connection.execute(
                "SELECT updated_at, device_id FROM sync_records WHERE kind = ? AND record_id = ?", (kind, record_id)
            ).fetchone()
            if existing and (timestamp_order(updated_at), device_id) <= (timestamp_order(existing[0]), existing[1]):
                continue
            encoded = None if deleted else json.dumps(change.get("payload"), ensure_ascii=False, separators=(",", ":"))
            revision += 1
            accepted += 1
            connection.execute(
                """INSERT INTO sync_records(kind, record_id, payload, updated_at, deleted, revision, device_id)
                   VALUES(?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(kind, record_id) DO UPDATE SET
                     payload=excluded.payload, updated_at=excluded.updated_at, deleted=excluded.deleted,
                     revision=excluded.revision, device_id=excluded.device_id""",
                (kind, record_id, encoded, updated_at, deleted, revision, device_id),
            )
        records = sync_rows(connection, since)
        server_revision = connection.execute("SELECT COALESCE(MAX(revision), 0) FROM sync_records").fetchone()[0]
    return {"ok": True, "accepted": accepted, "serverRevision": server_revision, "records": records}


def load_profile() -> dict[str, object] | None:
    with sqlite3.connect(PROFILE_DB) as connection:
        row = connection.execute("SELECT payload, updated_at FROM profiles WHERE id = ?", ("default",)).fetchone()
    if not row:
        return None
    try:
        return {"data": json.loads(row[0]), "updatedAt": row[1]}
    except json.JSONDecodeError:
        return None


def save_profile(payload: object) -> str:
    updated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with sqlite3.connect(PROFILE_DB) as connection:
        connection.execute(
            "INSERT INTO profiles(id, payload, updated_at) VALUES(?, ?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            ("default", encoded, updated_at),
        )
    return updated_at


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class RemovalError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def remove_book_paths(library_file: Path, data_dir: Path, book_id: str, paths: list[Path], library: list[dict[str, object]]) -> int:
    paths = [path.resolve() for path in paths]
    existing = [path for path in paths if path.exists()]
    freed = sum(directory_size(path) if path.is_dir() else path.stat().st_size for path in existing)
    updated = [item for item in library if item.get("id") != book_id]
    temporary = library_file.with_suffix(".json.removing")
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", book_id)[:80] or "book"
    trash = data_dir / f".remove-{safe_id}-{int(time.time() * 1000)}-{os.getpid()}"
    trash.mkdir(parents=True, exist_ok=False)
    moved: list[tuple[Path, Path]] = []
    try:
        for path in existing:
            destination = trash / path.name
            shutil.move(str(path), str(destination))
            moved.append((path, destination))
        book_bot.save_json(temporary, updated)
        temporary.replace(library_file)
        shutil.rmtree(trash, ignore_errors=True)
    except Exception as error:
        for original, destination in moved:
            if destination.exists() and not original.exists():
                shutil.move(str(destination), str(original))
        shutil.rmtree(trash, ignore_errors=True)
        temporary.unlink(missing_ok=True)
        raise RemovalError(f"移除失败：{error}", 500) from error
    return freed


def remove_library_entry(library_file: Path, data_dir: Path, entry: dict[str, object], library: list[dict[str, object]]) -> dict[str, object]:
    book_id = str(entry.get("id", ""))
    asset_base = str(entry.get("assetBase", ""))
    library_file = library_file.resolve()
    dist_root = library_file.parent.parent.resolve()
    assets_root = library_file.parent.resolve()

    if asset_base == f"books/{book_id}":
        books_root = (dist_root / "books").resolve()
        target = (books_root / book_id).resolve()
        if target.parent != books_root or target.name != book_id:
            raise RemovalError("书籍标识无效", 400)
        if not target.is_dir():
            raise RemovalError("书籍页面目录不存在", 404)
        freed = remove_book_paths(library_file, data_dir, book_id, [target], library)
        return {"freedBytes": freed, "legacy": False}

    if asset_base == "assets":
        if sum(1 for item in library if str(item.get("assetBase", "")) == "assets") > 1:
            raise RemovalError("这本书的目录与其他书共用，暂不自动移除", 409)
        paths = [assets_root / "book.json", assets_root / "conversion.json", assets_root / "pages"]
        if not any(path.exists() for path in paths):
            raise RemovalError("书籍页面文件不存在", 404)
        freed = remove_book_paths(library_file, data_dir, book_id, paths, library)
        return {"freedBytes": freed, "legacy": True}

    raise RemovalError("这本书使用未知的存储结构，暂不允许自动移除", 409)


def write_status(**values: object) -> None:
    book_bot.save_json(book_bot.STATE_FILE, {
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        **values,
    })


def load_publish_config() -> dict[str, object]:
    config = book_bot.load_json(PUBLISH_CONFIG_FILE, None)
    if not isinstance(config, dict):
        config = {}
    return {**PUBLISH_DEFAULT_CONFIG, **config}


def ensure_publish_config() -> None:
    if not PUBLISH_CONFIG_FILE.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        book_bot.save_json(PUBLISH_CONFIG_FILE, PUBLISH_DEFAULT_CONFIG)


def published_map() -> dict[str, object]:
    value = book_bot.load_json(PUBLISHED_FILE, {})
    return value if isinstance(value, dict) else {}


def save_published(book_id: str, info: dict[str, object]) -> None:
    data = published_map()
    data[book_id] = info
    book_bot.save_json(PUBLISHED_FILE, data)


def set_cloud_state(**values: object) -> None:
    with CLOUD_LOCK:
        CLOUD_STATE.update(values)


def http_json(method: str, url: str, token: str, payload: object | None = None, timeout: int = 60) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"error": raw or f"HTTP {error.code}"}
        raise RuntimeError(parsed.get("error") or f"HTTP {error.code}")
    except urllib.error.URLError as error:
        raise RuntimeError(f"无法连接发布服务器：{error.reason}")


def put_chunk(url: str, token: str, chunk: bytes, timeout: int = 120) -> dict:
    request = urllib.request.Request(
        url,
        data=chunk,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream", "Accept": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def publish_worker(book_id: str) -> None:
    try:
        config = load_publish_config()
        server_base = str(config.get("serverBase", "")).rstrip("/")
        token = str(config.get("publishToken", "")).strip()
        try:
            chunk_bytes = int(config.get("chunkBytes") or 4 * 1024 * 1024)
        except (TypeError, ValueError):
            chunk_bytes = 4 * 1024 * 1024
        chunk_bytes = max(64 * 1024, min(chunk_bytes, 8 * 1024 * 1024))
        if not server_base or not token:
            raise RuntimeError("未配置发布服务器或发布令牌（data/publish-config.json）")

        manifest, _book_json = publish_manifest.build_manifest(DIST, book_id)
        book_root = DIST / "books" / book_id
        set_cloud_state(state="planning", message="正在检查服务器…", percent=0, sentBytes=0, totalBytes=manifest["totalBytes"], error="", freeBytes=0)
        plan = http_json("POST", f"{server_base}/api/publish/plan", token, {"manifest": manifest})
        if not plan.get("ok"):
            raise RuntimeError(plan.get("error") or "服务器拒绝了发布请求")
        if plan.get("upToDate"):
            save_published(book_id, {"publishedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "bytes": manifest["totalBytes"], "title": manifest["title"], "serverBase": server_base})
            set_cloud_state(state="done", percent=100, sentBytes=manifest["totalBytes"], totalBytes=manifest["totalBytes"], message="云端已是最新版本", freeBytes=plan.get("freeBytes", 0))
            return
        if not plan.get("canProceed"):
            raise RuntimeError("服务器剩余空间不足，已停止上传")

        session = str(plan.get("sessionId") or "")
        if not session:
            raise RuntimeError("服务器未返回上传会话")
        file_sizes = {item["path"]: item["size"] for item in manifest["files"]}
        missing_set = set(plan.get("missingFiles") or [])
        missing = [item["path"] for item in manifest["files"] if item["path"] in missing_set]
        required = sum(file_sizes[path] for path in missing)
        sent = 0
        set_cloud_state(state="uploading", message="正在上传…", totalBytes=required, sentBytes=0, percent=0, freeBytes=plan.get("freeBytes", 0))

        for relative in missing:
            source = book_root / relative
            total_size = file_sizes[relative]
            offset = 0
            while offset < total_size:
                with source.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read(chunk_bytes)
                if not chunk:
                    raise RuntimeError(f"读取本地文件失败：{relative}")
                url = (
                    f"{server_base}/api/publish/file"
                    f"?session={urllib.parse.quote(session)}"
                    f"&path={urllib.parse.quote(relative, safe='')}"
                    f"&offset={offset}"
                )
                accepted = False
                attempt = 0
                while True:
                    try:
                        payload = put_chunk(url, token, chunk)
                        if not payload.get("ok"):
                            raise RuntimeError(payload.get("error") or "上传失败")
                        accepted = True
                        break
                    except urllib.error.HTTPError as error:
                        raw = error.read().decode("utf-8", "replace")
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError:
                            parsed = {"error": raw or f"HTTP {error.code}"}
                        if error.code == 409:
                            status = http_json("GET", f"{server_base}/api/publish/status?session={urllib.parse.quote(session)}", token)
                            entry = next((item for item in status.get("files", []) if item.get("path") == relative), None)
                            if not entry:
                                raise RuntimeError("服务器未返回文件进度")
                            new_offset = int(entry.get("received", 0))
                            if new_offset == offset:
                                attempt += 1
                                if attempt >= 3:
                                    raise RuntimeError("上传偏移无法同步，请重试")
                                time.sleep(1.0 * attempt)
                            else:
                                offset = new_offset
                            accepted = False
                            break
                        attempt += 1
                        if attempt >= 3:
                            raise RuntimeError(parsed.get("error") or f"上传失败（HTTP {error.code}）")
                        time.sleep(1.5 * attempt)
                    except (urllib.error.URLError, TimeoutError, ValueError) as error:
                        attempt += 1
                        if attempt >= 3:
                            raise RuntimeError(f"上传失败：{error}")
                        time.sleep(1.5 * attempt)
                if not accepted:
                    continue
                offset += len(chunk)
                sent += len(chunk)
                percent = round(sent / required * 100) if required else 100
                set_cloud_state(sentBytes=sent, percent=percent, message=f"正在上传 {percent}%")

        set_cloud_state(state="committing", message="正在校验并切换版本…", percent=100)
        commit = http_json("POST", f"{server_base}/api/publish/commit?session={urllib.parse.quote(session)}", token, None)
        if not commit.get("ok"):
            raise RuntimeError(commit.get("error") or "服务器提交失败")
        save_published(book_id, {"publishedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "bytes": manifest["totalBytes"], "title": manifest["title"], "serverBase": server_base})
        set_cloud_state(state="done", percent=100, sentBytes=required, totalBytes=required, message="发布成功", freeBytes=commit.get("freeBytes", 0))
    except Exception as error:
        set_cloud_state(state="failed", error=str(error), message=f"发布失败：{error}")


def start_cloud_publish(book_id: str) -> None:
    with CLOUD_LOCK:
        if CLOUD_STATE.get("state") in ("planning", "uploading", "committing"):
            raise ValueError("已有发布任务在进行，请稍候")
        CLOUD_STATE.update({
            "state": "planning", "bookId": book_id, "percent": 0, "sentBytes": 0,
            "totalBytes": 0, "message": "准备发布…", "error": "", "freeBytes": 0,
        })
    threading.Thread(target=publish_worker, args=(book_id,), daemon=True).start()


class LibraryHandler(SimpleHTTPRequestHandler):
    server_version = "OnePageLibrary/0.3"
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(DIST), **kwargs)

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
        if route == "/api/cloud-publish/status":
            with CLOUD_LOCK:
                self.send_json(dict(CLOUD_STATE))
            return
        if route == "/api/cloud-publish/library":
            config = load_publish_config()
            self.send_json({
                "ok": True,
                "configured": bool(config.get("publishToken")),
                "serverBase": config.get("serverBase", ""),
                "published": published_map(),
            })
            return
        local_match = re.fullmatch(r"/api/local-books/([^/]+)/(metadata|source)", route)
        if route == "/api/local-library":
            if not DESKTOP_MODE:
                self.send_json({"books": [], "desktopMode": False})
                return
            self.send_json({"books": [{key: value for key, value in item.items() if key != "sourcePath"} for item in local_books()], "desktopMode": True})
            return
        if local_match:
            if not DESKTOP_MODE:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            item = local_book(local_match.group(1))
            if not item:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if local_match.group(2) == "metadata":
                self.send_json({key: value for key, value in item.items() if key != "sourcePath"})
            else:
                self.serve_pdf(Path(str(item["sourcePath"])))
            return
        if route == "/api/status":
            status = book_bot.load_json(book_bot.STATE_FILE, {"status": "idle", "message": "等待导入书籍"})
            if status.get("status") == "idle":
                status["message"] = "书籍助手准备就绪"
            disk = shutil.disk_usage(ROOT)
            status["disk"] = {"free": disk.free, "total": disk.total}
            status["available"] = True
            self.send_json(status)
            return
        if route == "/api/version":
            self.send_json({"version": APP_VERSION, "mode": "desktop"})
            return
        if route == "/api/library-info":
            library = book_bot.load_json(book_bot.LIBRARY_FILE, [])
            entries = library if isinstance(library, list) else []
            result = []
            for item in entries:
                book_id = str(item.get("id", ""))
                asset_base = str(item.get("assetBase", ""))
                legacy = asset_base == "assets"
                shared = sum(1 for other in entries if str(other.get("assetBase", "")) == asset_base) > 1
                removable = asset_base == f"books/{book_id}" or (legacy and not shared)
                folder = (DIST / asset_base).resolve()
                dist_root = DIST.resolve()
                size = directory_size(folder) if (folder == dist_root or dist_root in folder.parents) and folder.is_dir() else 0
                result.append({"id": book_id, "outputBytes": size, "removable": removable, "legacy": legacy, "shared": shared})
            self.send_json({"books": result})
            return
        if route == "/api/profile":
            self.send_json({"profile": load_profile()})
            return
        if route == "/api/sync":
            try:
                since = max(0, int(urllib.parse.parse_qs(parsed.query).get("since", ["0"])[0]))
                with sqlite3.connect(PROFILE_DB) as connection:
                    records = sync_rows(connection, since)
                    revision = connection.execute("SELECT COALESCE(MAX(revision), 0) FROM sync_records").fetchone()[0]
                self.send_json({"ok": True, "serverRevision": revision, "records": records})
            except ValueError as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            return
        super().do_GET()

    def do_PUT(self) -> None:
        route = urllib.parse.urlparse(self.path).path
        if route != "/api/profile":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4 * 1024 * 1024:
                raise ValueError("阅读数据大小无效")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("阅读数据格式无效")
            updated_at = save_profile(payload)
            self.send_json({"ok": True, "updatedAt": updated_at})
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.send_json({"ok": False, "error": str(error)}, 400)

    def do_POST(self) -> None:
        route = urllib.parse.urlparse(self.path).path
        if route == "/api/cloud-publish":
            if not DESKTOP_MODE:
                self.send_json({"ok": False, "error": "发布仅在 BookBot 桌面端可用"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 64 * 1024:
                    raise ValueError("请求内容无效")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                book_id = str(payload.get("id", "")).strip()
                book_root = (DIST / "books" / book_id).resolve()
                if book_root.parent != (DIST / "books").resolve() or not book_root.is_dir():
                    raise ValueError("找不到这本书的流式版本")
                start_cloud_publish(book_id)
                self.send_json({"ok": True, "bookId": book_id})
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            return
        if route == "/api/sync":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 8 * 1024 * 1024:
                    raise ValueError("同步数据大小无效")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("同步数据格式无效")
                self.send_json(sync_changes(payload))
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
                self.send_json({"ok": False, "error": str(error)}, 400)
            return
        if route == "/api/open-local-pdf":
            self.open_local_pdf()
            return
        if route == "/api/publish-local":
            self.publish_local_pdf()
            return
        if route == "/api/remove-book":
            self.remove_book()
            return
        if route != "/api/import":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        raw_name = self.headers.get("X-Book-Name", "book.pdf")
        name = Path(urllib.parse.unquote(raw_name)).name.strip()
        if not name.lower().endswith(".pdf"):
            self.send_json({"ok": False, "error": "只支持 PDF 文件"}, 400)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 5 * 1024**3:
            self.send_json({"ok": False, "error": "文件大小无效或超过 5GB"}, 400)
            return

        INBOX.mkdir(parents=True, exist_ok=True)
        target = INBOX / name
        if target.exists():
            target = INBOX / f"{target.stem}-{int(time.time())}{target.suffix}"
        temporary = target.with_suffix(target.suffix + ".uploading")
        write_status(status="uploading", book=target.stem, percent=0, message=f"正在接收《{target.stem}》")
        remaining = length
        try:
            with temporary.open("wb") as stream:
                while remaining:
                    chunk = self.rfile.read(min(8 * 1024 * 1024, remaining))
                    if not chunk:
                        raise ConnectionError("文件传输提前中断")
                    stream.write(chunk)
                    remaining -= len(chunk)
            temporary.replace(target)
            write_status(status="queued", book=target.stem, percent=0, message=f"《{target.stem}》已进入处理队列")
            self.send_json({"ok": True, "name": target.name})
        except Exception as error:
            temporary.unlink(missing_ok=True)
            write_status(status="failed", book=target.stem, error=str(error), message=f"导入失败：{error}")
            self.send_json({"ok": False, "error": str(error)}, 500)

    def open_local_pdf(self) -> None:
        if not DESKTOP_MODE:
            self.send_json({"ok": False, "error": "直接阅读仅在 BookBot 桌面端可用"}, 403)
            return
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askopenfilename(title="选择要直接阅读的 PDF", filetypes=[("PDF 文件", "*.pdf")])
            root.destroy()
            if not selected:
                self.send_json({"ok": False, "cancelled": True})
                return
            metadata = register_local_pdf(Path(selected))
            self.send_json({"ok": True, "book": {key: value for key, value in metadata.items() if key != "sourcePath"}})
        except Exception as error:
            self.send_json({"ok": False, "error": f"无法打开 PDF：{error}"}, 500)

    def publish_local_pdf(self) -> None:
        if not DESKTOP_MODE:
            self.send_json({"ok": False, "error": "流式生成请在 BookBot 桌面端操作"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("请求内容无效")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            item = local_book(str(payload.get("id", "")))
            if not item:
                raise ValueError("本机书库中没有这本书")
            source = Path(str(item["sourcePath"]))
            PROCESS_QUEUE.put(source)
            write_status(status="queued", book=item["title"], percent=0, message=f"《{item['title']}》已加入流式生成队列")
            self.send_json({"ok": True, "bookId": item["id"]})
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.send_json({"ok": False, "error": str(error)}, 400)

    def serve_pdf(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range", "")
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip()) if range_header else None
        status = HTTPStatus.OK
        if match:
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else min(size - 1, start + 1024 * 1024 - 1)
            elif match.group(2):
                start = max(0, size - int(match.group(2)))
            if start >= size or start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "private, no-store")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as stream:
                stream.seek(start, os.SEEK_SET)
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def remove_book(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("请求内容无效")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            book_id = str(payload.get("id", "")).strip()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.send_json({"ok": False, "error": str(error)}, 400)
            return

        if not book_id or book_id in {".", ".."} or "/" in book_id or "\\" in book_id:
            self.send_json({"ok": False, "error": "书籍标识无效"}, 400)
            return

        with LIBRARY_LOCK:
            library = book_bot.load_json(book_bot.LIBRARY_FILE, [])
            entry = next((item for item in library if item.get("id") == book_id), None) if isinstance(library, list) else None
            if not entry:
                local = book_bot.load_json(LOCAL_BOOKS_FILE, [])
                local_entry = next((item for item in local if item.get("id") == book_id), None) if isinstance(local, list) else None
                if not local_entry:
                    self.send_json({"ok": False, "error": "书架中没有这本书"}, 404)
                    return
                unregister_local_book(book_id)
                forget_book_index(book_id)
                self.send_json({"ok": True, "id": book_id, "localOnly": True, "localRemoved": True, "sourcePreserved": True, "freedBytes": 0})
                return
            try:
                result = remove_library_entry(book_bot.LIBRARY_FILE, DATA, entry, library)
                local_removed = unregister_local_book(book_id)
                forget_book_index(book_id)
                self.send_json({"ok": True, "id": book_id, "sourcePreserved": True, "localRemoved": local_removed, **result})
            except RemovalError as error:
                self.send_json({"ok": False, "error": str(error)}, error.status)

    def log_message(self, format_string: str, *args: object) -> None:
        if args and isinstance(args[0], str) and args[0].startswith("GET /api/status"):
            return
        print(f"[Library] {self.address_string()} - {format_string % args}")


def worker() -> None:
    while True:
        try:
            try:
                source = PROCESS_QUEUE.get_nowait()
            except queue.Empty:
                source = None
            if source:
                state = book_bot.load_json(book_bot.INDEX_FILE, {"completed": {}})
                book_bot.process(source, state)
            book_bot.scan(INBOX, stable_seconds=8)
        except Exception as error:
            write_status(status="failed", error=str(error), message=f"处理服务异常：{error}")
        time.sleep(5)


def main() -> None:
    global DESKTOP_MODE
    parser = argparse.ArgumentParser(description="BookBot personal library")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--desktop", action="store_true", help="Enable local PDF access and desktop-only actions")
    args = parser.parse_args()
    DESKTOP_MODE = args.desktop
    init_profile_db()
    ensure_publish_config()
    INBOX.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), LibraryHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"个人图书馆已启动：{url}")
    print("关闭这个窗口会停止本地服务，但不会丢失书籍或进度。")
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n个人图书馆已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
