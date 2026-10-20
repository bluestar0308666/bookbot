"""Test the local server.py publish orchestration against a temp cloud instance."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOUD_SERVER = ROOT / "release" / "bookbot" / "app" / "server_readonly.py"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import server  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_book(dist: Path, book_id: str, title: str, pages: int = 3, page_size: int = 9000) -> Path:
    book = dist / "books" / book_id
    (book / "pages").mkdir(parents=True, exist_ok=True)
    (book / "book.json").write_text(json.dumps({
        "id": book_id, "title": title, "assetBase": f"books/{book_id}",
        "sourceName": f"{title}.pdf", "sourceBytes": 123, "pageCount": pages, "outline": [],
    }, ensure_ascii=False), encoding="utf-8")
    (book / "conversion.json").write_text(json.dumps({"pageCount": pages}), encoding="utf-8")
    for index in range(1, pages + 1):
        (book / "pages" / f"page-{index}.jpg").write_bytes(b"JPEG" + bytes([index]) * page_size)
    return book


def reset_state() -> None:
    with server.CLOUD_LOCK:
        server.CLOUD_STATE.update({
            "state": "idle", "bookId": "", "percent": 0, "sentBytes": 0,
            "totalBytes": 0, "message": "", "error": "", "freeBytes": 0,
        })


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="bookbot-client-"))
    dist = work / "dist"
    library = work / "library"
    cloud_data = work / "cloud-data"
    (library / "assets").mkdir(parents=True)
    (library / "books").mkdir(parents=True)
    cloud_data.mkdir(parents=True)
    (library / "assets" / "library.json").write_text("[]", encoding="utf-8")

    book_id = "book-client1234"
    make_book(dist, book_id, "客户端测试书")

    token = "client-publish-token"
    port = free_port()
    env = {**os.environ, "READER_PASSWORD": "pw", "READER_USERNAME": "reader", "BOOKBOT_PUBLISH_TOKEN": token}
    process = subprocess.Popen(
        [sys.executable, str(CLOUD_SERVER), "--host", "127.0.0.1", "--port", str(port),
         "--directory", str(library), "--data", str(cloud_data)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        for _ in range(50):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        else:
            raise RuntimeError("cloud server did not start")

        server.DIST = dist
        server.PUBLISH_CONFIG_FILE = work / "publish-config.json"
        server.PUBLISHED_FILE = work / "published.json"

        server.PUBLISH_CONFIG_FILE.write_text(json.dumps({
            "serverBase": f"http://127.0.0.1:{port}",
            "publishToken": token,
            "chunkBytes": 4096,
        }), encoding="utf-8")

        reset_state()
        server.publish_worker(book_id)
        assert server.CLOUD_STATE["state"] == "done", server.CLOUD_STATE
        assert server.CLOUD_STATE["percent"] == 100, server.CLOUD_STATE
        assert (library / "books" / book_id / "book.json").is_file()
        library_json = json.loads((library / "assets" / "library.json").read_text(encoding="utf-8"))
        assert any(item.get("id") == book_id for item in library_json), library_json
        published = json.loads(server.PUBLISHED_FILE.read_text(encoding="utf-8"))
        assert book_id in published, published

        # duplicate publish should be up-to-date
        reset_state()
        server.publish_worker(book_id)
        assert server.CLOUD_STATE["state"] == "done", server.CLOUD_STATE

        # wrong token must fail without touching the live version
        server.PUBLISH_CONFIG_FILE.write_text(json.dumps({
            "serverBase": f"http://127.0.0.1:{port}",
            "publishToken": "wrong-token",
            "chunkBytes": 4096,
        }), encoding="utf-8")
        reset_state()
        server.publish_worker(book_id)
        assert server.CLOUD_STATE["state"] == "failed", server.CLOUD_STATE
        assert (library / "books" / book_id / "book.json").is_file()

        print("CLIENT TESTS PASSED")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
