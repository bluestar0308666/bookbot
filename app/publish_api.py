"""Minimal publish API for BookBot's private cloud library.

Design goals:
- No new dependencies (stdlib only).
- Never touch reader.db or the sync protocol.
- Upload into a temp dir, verify every file, then atomically switch.
- Keep at most one previous version per book.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

ALLOWED_EXTENSIONS = {
    ".json", ".jpg", ".jpeg", ".png", ".webp", ".svg", ".gif",
    ".css", ".js", ".mjs", ".html", ".txt",
    ".woff", ".woff2", ".ttf", ".otf",
}
BOOK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PAGE_FILE_RE = re.compile(r"^pages/page-\d+\.jpg$")
SESSION_ID_RE = re.compile(r"^[a-f0-9]{32}$")

MAX_FILE_BYTES = 64 * 1024 * 1024
SAFETY_MARGIN_BYTES = 1024 * 1024 * 1024
SESSION_TTL_SECONDS = 24 * 3600


class PublishError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def now_ts() -> int:
    return int(time.time())


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def load_json(path: Path, fallback: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def safe_relpath(raw: object) -> str:
    if not isinstance(raw, str):
        raise PublishError("文件路径无效")
    text = raw.replace("\\", "/").strip()
    if not text or "\x00" in text:
        raise PublishError("文件路径无效")
    if text.startswith("/"):
        raise PublishError("不允许绝对路径")
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise PublishError("不允许路径穿越")
        parts.append(part)
    if not parts:
        raise PublishError("文件路径无效")
    relative = "/".join(parts)
    suffix = Path(relative).suffix.lower()
    if suffix == ".pdf":
        raise PublishError("不允许上传原始 PDF")
    if suffix not in ALLOWED_EXTENSIONS:
        raise PublishError(f"不允许的文件类型：{suffix or '(无扩展名)'}")
    return relative


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def manifest_digest(manifest: dict) -> str:
    canonical = {
        "bookId": manifest.get("bookId"),
        "title": manifest.get("title"),
        "assetBase": manifest.get("assetBase"),
        "pageCount": manifest.get("pageCount"),
        "files": manifest.get("files"),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PublishManager:
    def __init__(self, library_root: Path, data_dir: Path, token: str = "") -> None:
        self.library_root = Path(library_root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.token = str(token or "").strip()
        self.books_root = self.library_root / "books"
        self.assets_root = self.library_root / "assets"
        self.temp_root = self.library_root / ".publish"
        self.previous_root = self.library_root / ".previous"
        self.sessions_root = self.data_dir / "publish" / "sessions"
        self.lock = threading.RLock()
        for folder in (self.books_root, self.assets_root, self.temp_root, self.previous_root, self.sessions_root):
            folder.mkdir(parents=True, exist_ok=True)

    # ---------- auth ----------
    def auth_ok(self, authorization: str) -> bool:
        if not self.token:
            raise PublishError("发布接口未启用（未配置 BOOKBOT_PUBLISH_TOKEN）", 503)
        prefix = "Bearer "
        supplied = str(authorization or "")
        if not supplied.startswith(prefix):
            raise PublishError("缺少发布令牌", 401)
        return hmac.compare_digest(supplied[len(prefix):].strip(), self.token)

    # ---------- helpers ----------
    def free_bytes(self) -> int:
        return shutil.disk_usage(self.library_root).free

    def session_file(self, session_id: str) -> Path:
        if not SESSION_ID_RE.fullmatch(str(session_id or "")):
            raise PublishError("会话不存在", 404)
        return self.sessions_root / f"{session_id}.json"

    def load_session(self, session_id: str) -> dict:
        value = load_json(self.session_file(session_id), None)
        if not isinstance(value, dict):
            raise PublishError("会话不存在", 404)
        if int(value.get("expiresAt", 0)) <= now_ts():
            raise PublishError("会话已过期，请重新开始", 410)
        return value

    def save_session(self, session: dict) -> None:
        atomic_write_json(self.session_file(session["sessionId"]), session)

    def book_dir(self, book_id: str) -> Path:
        if not BOOK_ID_RE.fullmatch(str(book_id or "")):
            raise PublishError("书籍 ID 无效")
        return self.books_root / book_id

    def validate_manifest(self, manifest: object) -> dict:
        if not isinstance(manifest, dict):
            raise PublishError("manifest 格式无效")
        book_id = str(manifest.get("bookId", ""))
        if not BOOK_ID_RE.fullmatch(book_id):
            raise PublishError("manifest.bookId 无效")
        asset_base = str(manifest.get("assetBase", ""))
        if asset_base != f"books/{book_id}":
            raise PublishError("manifest.assetBase 与 bookId 不一致")
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise PublishError("manifest.files 为空")
        seen: set[str] = set()
        total = 0
        normalized = []
        for item in files:
            if not isinstance(item, dict):
                raise PublishError("manifest.files 格式无效")
            relative = safe_relpath(item.get("path"))
            if relative in seen:
                raise PublishError(f"重复文件：{relative}")
            seen.add(relative)
            try:
                size = int(item.get("size"))
            except (TypeError, ValueError):
                raise PublishError(f"文件大小无效：{relative}")
            if size < 0 or size > MAX_FILE_BYTES:
                raise PublishError(f"文件过大：{relative}")
            digest = str(item.get("sha256", "")).lower()
            if not SHA256_RE.fullmatch(digest):
                raise PublishError(f"校验值无效：{relative}")
            total += size
            normalized.append({"path": relative, "size": size, "sha256": digest})
        if "book.json" not in seen:
            raise PublishError("manifest 必须包含 book.json")
        page_files = [item["path"] for item in normalized if PAGE_FILE_RE.fullmatch(item["path"])]
        try:
            page_count = int(manifest.get("pageCount"))
        except (TypeError, ValueError):
            page_count = 0
        if page_count > 0 and len(page_files) != page_count:
            raise PublishError(f"页面数量不一致：book.json={page_count}，实际文件={len(page_files)}")
        validated = {
            "bookId": book_id,
            "title": str(manifest.get("title", "")).strip() or book_id,
            "assetBase": asset_base,
            "pageCount": page_count,
            "totalBytes": total,
            "files": normalized,
        }
        validated["manifestSha256"] = manifest_digest(validated)
        return validated

    def existing_manifest(self, book_id: str) -> dict | None:
        value = load_json(self.book_dir(book_id) / "manifest.json", None)
        return value if isinstance(value, dict) else None

    # ---------- API ----------
    def plan(self, body: object) -> dict:
        if not isinstance(body, dict):
            raise PublishError("请求格式无效")
        manifest = self.validate_manifest(body.get("manifest"))
        book_id = manifest["bookId"]
        existing = self.existing_manifest(book_id)
        if existing and existing.get("manifestSha256") == manifest["manifestSha256"]:
            return {
                "ok": True, "upToDate": True, "bookId": book_id, "sessionId": "",
                "freeBytes": self.free_bytes(),
            }

        existing_files: dict[str, tuple[int, str]] = {}
        if isinstance(existing, dict):
            for item in existing.get("files", []) if isinstance(existing.get("files"), list) else []:
                try:
                    existing_files[safe_relpath(item.get("path"))] = (int(item.get("size")), str(item.get("sha256", "")).lower())
                except (PublishError, TypeError, ValueError):
                    continue

        missing = []
        missing_set: set[str] = set()
        for item in manifest["files"]:
            source = self.book_dir(book_id) / Path(*item["path"].split("/"))
            if not source.is_file() or existing_files.get(item["path"]) != (item["size"], item["sha256"]):
                missing.append(item["path"])
                missing_set.add(item["path"])
        required = sum(item["size"] for item in manifest["files"] if item["path"] in missing_set)
        free = self.free_bytes()
        can_proceed = (free - SAFETY_MARGIN_BYTES) >= required

        if not can_proceed:
            return {
                "ok": True, "upToDate": False, "sessionId": "", "bookId": book_id,
                "missingFiles": missing, "totalBytes": manifest["totalBytes"],
                "requiredBytes": required, "freeBytes": free,
                "safeFreeBytes": max(0, free - SAFETY_MARGIN_BYTES),
                "canProceed": False, "maxFileBytes": MAX_FILE_BYTES,
            }

        session_id = uuid.uuid4().hex
        session = {
            "sessionId": session_id,
            "bookId": book_id,
            "manifest": manifest,
            "missing": missing,
            "state": "open",
            "createdAt": now_ts(),
            "expiresAt": now_ts() + SESSION_TTL_SECONDS,
        }
        self.save_session(session)
        temp_dir = self.temp_root / session_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        # Seed unchanged files into the temp dir so commit always verifies a
        # complete candidate. Hardlink when possible; copy otherwise.
        for item in manifest["files"]:
            if item["path"] in missing_set:
                continue
            source = self.book_dir(book_id) / Path(*item["path"].split("/"))
            target = temp_dir / Path(*item["path"].split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
        return {
            "ok": True, "upToDate": False, "sessionId": session_id, "bookId": book_id,
            "missingFiles": missing, "totalBytes": manifest["totalBytes"],
            "requiredBytes": required, "freeBytes": free,
            "safeFreeBytes": max(0, free - SAFETY_MARGIN_BYTES),
            "canProceed": True, "maxFileBytes": MAX_FILE_BYTES,
        }

    def write_chunk(self, session_id: str, raw_path: str, offset: int, data: bytes) -> dict:
        with self.lock:
            session = self.load_session(session_id)
            if session.get("state") != "open":
                raise PublishError("会话状态不允许上传", 409)
            relative = safe_relpath(raw_path)
            entry = next((item for item in session["manifest"]["files"] if item["path"] == relative), None)
            if not entry:
                raise PublishError("该文件不在 manifest 中", 400)
            target = self.temp_root / session_id / Path(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            current = target.stat().st_size if target.exists() else 0
            if int(offset) != current:
                raise PublishError(f"偏移不一致，期望 {current}", 409)
            if current + len(data) > entry["size"]:
                raise PublishError("上传数据超过文件声明大小", 400)
            with target.open("ab") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            received = target.stat().st_size
            return {"ok": True, "path": relative, "received": received, "total": entry["size"], "done": received == entry["size"]}

    def status(self, session_id: str) -> dict:
        with self.lock:
            session = self.load_session(session_id)
            progress = []
            for item in session["manifest"]["files"]:
                target = self.temp_root / session_id / Path(*item["path"].split("/"))
                received = target.stat().st_size if target.exists() else 0
                progress.append({"path": item["path"], "received": received, "total": item["size"], "done": received == item["size"]})
            received_total = sum(item["received"] for item in progress)
            total = session["manifest"]["totalBytes"] or 1
            return {
                "ok": True, "sessionId": session_id, "bookId": session["bookId"],
                "state": session.get("state"), "totalBytes": session["manifest"]["totalBytes"],
                "receivedBytes": received_total, "percent": round(received_total / total * 100),
                "files": progress,
            }

    def commit(self, session_id: str) -> dict:
        with self.lock:
            session = self.load_session(session_id)
            if session.get("state") == "done":
                return {"ok": True, "sessionId": session_id, "bookId": session["bookId"], "alreadyCommitted": True}
            if session.get("state") != "open":
                raise PublishError("会话状态不允许提交", 409)
            manifest = session["manifest"]
            book_id = manifest["bookId"]
            temp_dir = self.temp_root / session_id

            for item in manifest["files"]:
                target = temp_dir / Path(*item["path"].split("/"))
                if not target.exists() or target.stat().st_size != item["size"]:
                    raise PublishError(f"文件未上传完整：{item['path']}")
                if sha256_file(target) != item["sha256"]:
                    raise PublishError(f"文件校验失败：{item['path']}")

            try:
                book_json = json.loads((temp_dir / "book.json").read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as error:
                raise PublishError(f"book.json 无效：{error}")
            if str(book_json.get("id", "")) != book_id:
                raise PublishError("book.json 中的 id 与 manifest 不一致")
            if str(book_json.get("assetBase", "")) != f"books/{book_id}":
                raise PublishError("book.json 中的 assetBase 不正确")

            target_dir = self.book_dir(book_id)
            previous_dir = self.previous_root / book_id
            if previous_dir.exists():
                shutil.rmtree(previous_dir)
            if target_dir.exists():
                previous_dir.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target_dir, previous_dir)
            try:
                os.replace(temp_dir, target_dir)
            except Exception:
                if previous_dir.exists() and not target_dir.exists():
                    os.replace(previous_dir, target_dir)
                raise

            atomic_write_json(target_dir / "manifest.json", {**manifest, "committedAt": now_ts()})

            library_file = self.assets_root / "library.json"
            library = load_json(library_file, [])
            if not isinstance(library, list):
                library = []
            entry = {
                "id": book_id,
                "title": manifest["title"],
                "metadata": f"books/{book_id}/book.json",
                "assetBase": f"books/{book_id}",
                "cover": f"books/{book_id}/pages/page-1.jpg",
            }
            library = [item for item in library if not isinstance(item, dict) or item.get("id") != book_id]
            library.append(entry)
            atomic_write_json(library_file, library)

            session["state"] = "done"
            session["committedAt"] = now_ts()
            self.save_session(session)
            return {
                "ok": True, "sessionId": session_id, "bookId": book_id,
                "bytes": manifest["totalBytes"], "freeBytes": self.free_bytes(),
            }

    def library_snapshot(self) -> dict:
        library = load_json(self.assets_root / "library.json", [])
        published = {}
        if isinstance(library, list):
            for item in library:
                if not isinstance(item, dict):
                    continue
                book_id = str(item.get("id", ""))
                if not book_id:
                    continue
                folder = self.library_root / str(item.get("assetBase", ""))
                size = sum(path.stat().st_size for path in folder.rglob("*") if path.is_file()) if folder.is_dir() else 0
                published[book_id] = {"title": item.get("title", ""), "bytes": size}
        return {"ok": True, "published": published, "freeBytes": self.free_bytes()}
