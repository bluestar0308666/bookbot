from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INBOX = ROOT / "待处理书籍"
DIST = ROOT / "dist"
LIBRARY_FILE = DIST / "assets" / "library.json"
STATE_FILE = ROOT / "book-bot-state.json"
INDEX_FILE = ROOT / "book-bot-index.json"


def flatten_outline(reader: PdfReader) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []

    def visit(items: list[object], depth: int = 0) -> None:
        for item in items:
            if isinstance(item, list):
                visit(item, depth + 1)
                continue
            try:
                page_index = reader.get_destination_page_number(item)
            except Exception:
                page_index = None
            result.append({
                "title": getattr(item, "title", str(item)),
                "pageIndex": page_index,
                "pageNumber": page_index + 1 if page_index is not None else None,
                "depth": depth,
            })

    if isinstance(reader.outline, list):
        visit(reader.outline)
    return result


def load_json(path: Path, fallback: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def fingerprint(pdf: Path) -> str:
    stat = pdf.stat()
    raw = f"{pdf.name}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def stable(pdf: Path, seconds: int) -> bool:
    return time.time() - pdf.stat().st_mtime >= seconds


def update_status(**values: object) -> None:
    previous = load_json(STATE_FILE, {})
    if isinstance(previous, dict) and all(previous.get(key) == value for key, value in values.items()):
        return
    payload = {"updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"), **values}
    save_json(STATE_FILE, payload)
    message = payload.get("message") or payload.get("status")
    print(f"[Book Bot] {message}", flush=True)


def process(pdf: Path, state: dict[str, object]) -> None:
    mark = fingerprint(pdf)
    completed = state.setdefault("completed", {})
    book_id = f"book-{mark[:10]}"
    title = pdf.stem.strip()
    book_root = DIST / "books" / book_id
    page_dir = book_root / "pages"
    if mark in completed:
        ready = False
        if (book_root / "book.json").is_file() and page_dir.is_dir():
            try:
                metadata = load_json(book_root / "book.json", {})
                conversion = load_json(book_root / "conversion.json", {})
                expected = int(metadata.get("pageCount") or 0)
                actual = sum(1 for _ in page_dir.glob("page-*.jpg"))
                error_pages = conversion.get("errorPages") if isinstance(conversion, dict) else None
                ready = expected > 0 and actual >= expected and not error_pages
            except (OSError, TypeError, ValueError):
                ready = False
        if ready:
            return
        completed.pop(mark, None)
        save_json(INDEX_FILE, state)

    update_status(status="inspecting", book=title, message=f"正在检查《{title}》")

    reader = PdfReader(pdf)
    page_count = len(reader.pages)
    outline = flatten_outline(reader)
    required = int(pdf.stat().st_size * 0.45)
    free = __import__("shutil").disk_usage(DIST).free
    if free < required + 512 * 1024 * 1024:
        raise RuntimeError("磁盘空间不足，已安全停止；没有删除任何文件。")

    update_status(status="converting", book=title, pageCount=page_count, message=f"开始转换《{title}》，共 {page_count} 页")
    command = [
        sys.executable, str(ROOT / "tools" / "convert_book.py"), str(pdf), str(page_dir),
        "--resume", "--workers", "2", "--width", "1200", "--quality", "78",
    ]
    process_handle = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process_handle.stdout is not None
    for line in process_handle.stdout:
        print(line, end="", flush=True)
        match = re.search(r"converted=(\d+)/(\d+)", line)
        if match:
            converted, pending = map(int, match.groups())
            percent = round(converted / pending * 100) if pending else 100
            update_status(
                status="converting",
                book=title,
                pageCount=page_count,
                converted=converted,
                pending=pending,
                percent=percent,
                message=f"正在处理《{title}》：{percent}%",
            )
        fallback = re.search(r"fallback=(\d+)/(\d+)", line)
        if fallback:
            done, total = map(int, fallback.groups())
            percent = round(90 + 10 * done / total) if total else 100
            update_status(
                status="converting",
                book=title,
                pageCount=page_count,
                converted=done,
                pending=total,
                percent=percent,
                message=f"正在补齐《{title}》页面：{done}/{total}",
            )
    if process_handle.wait() != 0:
        raise subprocess.CalledProcessError(process_handle.returncode, command)

    metadata = {
        "id": book_id,
        "title": title,
        "assetBase": f"books/{book_id}",
        "sourceName": pdf.name,
        "sourceBytes": pdf.stat().st_size,
        "pageCount": page_count,
        "outline": outline,
    }
    save_json(book_root / "book.json", metadata)
    library = load_json(LIBRARY_FILE, [])
    library = [item for item in library if item.get("id") != book_id]
    library.append({
        "id": book_id,
        "title": title,
        "metadata": f"books/{book_id}/book.json",
        "assetBase": f"books/{book_id}",
        "cover": f"books/{book_id}/pages/page-1.jpg",
    })
    save_json(LIBRARY_FILE, library)
    completed[mark] = {"bookId": book_id, "source": str(pdf), "completedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_json(INDEX_FILE, state)
    update_status(status="complete", book=title, pageCount=page_count, message=f"《{title}》已加入书架")


def scan(inbox: Path, stable_seconds: int) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    state = load_json(INDEX_FILE, {"completed": {}})
    found = False
    for pdf in sorted(inbox.glob("*.pdf")):
        found = True
        if not stable(pdf, stable_seconds):
            update_status(status="waiting", book=pdf.stem, message=f"等待《{pdf.stem}》复制完成")
            continue
        try:
            process(pdf, state)
        except Exception as error:
            update_status(status="failed", book=pdf.stem, error=str(error), message=f"《{pdf.stem}》处理失败：{error}")
    if not found and not any(inbox.glob("*.uploading")):
        update_status(status="idle", message=f"等待新书：请把 PDF 放入 {inbox}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch a folder and add large PDFs to the local reading workspace.")
    parser.add_argument("--inbox", type=Path, default=DEFAULT_INBOX)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--stable-seconds", type=int, default=15)
    args = parser.parse_args()
    if not args.watch:
        scan(args.inbox, args.stable_seconds)
        return
    while True:
        scan(args.inbox, args.stable_seconds)
        time.sleep(max(args.interval, 3))


if __name__ == "__main__":
    main()
