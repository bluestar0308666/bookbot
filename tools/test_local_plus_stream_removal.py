import json
import shutil
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, r"D:/codex/2026-10-06/d-open-handover-md-x20/reader-prototype")
import server

root = Path(tempfile.mkdtemp(prefix="bookbot-local-plus-stream-"))
try:
    dist = root / "dist"
    book = dist / "books" / "book-both"
    book.mkdir(parents=True)
    (book / "book.json").write_text('{"id":"book-both"}', encoding="utf-8")
    (book / "pages").mkdir()
    (book / "pages" / "page-1.jpg").write_bytes(b"x" * 1024)
    library_file = dist / "assets" / "library.json"
    library_file.parent.mkdir(parents=True)
    library = [{"id": "book-both", "assetBase": "books/book-both", "title": "Both"}]
    server.book_bot.save_json(library_file, library)

    source = root / "source" / "both.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4 test")
    local_file = root / "data" / "local-books.json"
    local_file.parent.mkdir(parents=True)
    local_file.write_text(json.dumps([
        {"id": "book-both", "title": "Both", "localDirect": True, "sourcePath": str(source), "pageCount": 1}
    ]), encoding="utf-8")

    server.DIST = dist
    server.DATA = root / "data"
    server.book_bot.LIBRARY_FILE = library_file
    server.LOCAL_BOOKS_FILE = local_file
    server.DESKTOP_MODE = True
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.LibraryHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({"id": "book-both"}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/remove-book", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode())
        assert result["ok"] is True, result
        assert result.get("localOnly") is not True, result
        assert result["localRemoved"] is True, result
        assert not book.exists(), "stream folder must be removed"
        assert json.loads(local_file.read_text(encoding="utf-8")) == [], "local registration must be removed"
        assert source.is_file(), "source PDF must be preserved"
        print("LOCAL_PLUS_STREAM_OK", result)
    finally:
        httpd.shutdown()
        httpd.server_close()
finally:
    shutil.rmtree(root, ignore_errors=True)
print("LOCAL PLUS STREAM REMOVAL TEST PASSED")
