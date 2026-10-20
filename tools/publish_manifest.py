"""Build a publish manifest from a local BookBot stream directory."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ALLOWED_EXTENSIONS = {
    ".json", ".jpg", ".jpeg", ".png", ".webp", ".svg", ".gif",
    ".css", ".js", ".mjs", ".html", ".txt",
    ".woff", ".woff2", ".ttf", ".otf",
}
PAGE_FILE_RE = re.compile(r"pages/page-\d+\.jpg$")


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


def build_manifest(dist_root: Path, book_id: str) -> tuple[dict, dict]:
    dist_root = Path(dist_root).resolve()
    books_root = (dist_root / "books").resolve()
    book_root = (books_root / book_id).resolve()
    if book_root.parent != books_root or not book_root.is_dir():
        raise ValueError("找不到这本书的流式版本目录")
    book_json_path = book_root / "book.json"
    if not book_json_path.is_file():
        raise ValueError("缺少 book.json")
    book_json = json.loads(book_json_path.read_text(encoding="utf-8"))
    if str(book_json.get("id", "")) != book_id:
        raise ValueError("book.json 中的 id 与书籍目录不一致")
    if str(book_json.get("assetBase", "")) != f"books/{book_id}":
        raise ValueError("book.json 中的 assetBase 不正确")

    files = []
    total = 0
    for path in sorted(book_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(book_root).as_posix()
        if relative == "manifest.json":
            continue
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            raise ValueError("不允许上传原始 PDF")
        if suffix not in ALLOWED_EXTENSIONS:
            continue
        size = path.stat().st_size
        files.append({"path": relative, "size": size, "sha256": sha256_file(path)})
        total += size

    if not files:
        raise ValueError("没有可上传的流式文件")
    page_files = [item for item in files if PAGE_FILE_RE.fullmatch(item["path"])]
    try:
        page_count = int(book_json.get("pageCount") or 0)
    except (TypeError, ValueError):
        page_count = 0
    if page_count and len(page_files) != page_count:
        raise ValueError(f"页面数量不一致：book.json={page_count}，实际文件={len(page_files)}")

    manifest = {
        "schemaVersion": 1,
        "bookId": book_id,
        "title": str(book_json.get("title") or book_id),
        "assetBase": f"books/{book_id}",
        "pageCount": page_count,
        "totalBytes": total,
        "files": files,
    }
    manifest["manifestSha256"] = manifest_digest(manifest)
    return manifest, book_json
