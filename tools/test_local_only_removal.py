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

root = Path(tempfile.mkdtemp(prefix="bookbot-local-only-removal-"))
try:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "library.json").write_text("[]", encoding="utf-8")
    source = root / "source" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4 test")
    local_file = root / "data" / "local-books.json"
    local_file.parent.mkdir(parents=True)
    local_file.write_text(json.dumps([
        {"id": "book-local", "title": "Local", "localDirect": True, "sourcePath": str(source), "pageCount": 19}
    ]), encoding="utf-8")

    server.DIST = dist
    server.DATA = root / "data"
    server.book_bot.LIBRARY_FILE = dist / "assets" / "library.json"
    server.LOCAL_BOOKS_FILE = local_file
    server.DESKTOP_MODE = True
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.LibraryHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({"id": "book-local"}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/remove-book", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode())
        assert result["ok"] is True, result
        assert result["localOnly"] is True, result
        assert result["freedBytes"] == 0, result
        assert json.loads(local_file.read_text(encoding="utf-8")) == []
        assert source.is_file(), "source PDF must be preserved"
        print("LOCAL_ONLY_ENDPOINT_OK", result)
    finally:
        httpd.shutdown()
        httpd.server_close()
finally:
    shutil.rmtree(root, ignore_errors=True)
print("LOCAL ONLY REMOVAL TEST PASSED")
