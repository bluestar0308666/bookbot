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

root = Path(tempfile.mkdtemp(prefix="bookbot-endpoint-test-"))
try:
    dist = root / "dist"
    assets = dist / "assets"
    pages = assets / "pages"
    pages.mkdir(parents=True)
    (assets / "book.json").write_text('{"id":"legacy"}', encoding="utf-8")
    (assets / "conversion.json").write_text('{"pageCount":1}', encoding="utf-8")
    (pages / "page-1.jpg").write_bytes(b"y" * 4096)
    (assets / "local-pdf-cover.svg").write_text("<svg/>", encoding="utf-8")
    library = [{"id": "legacy", "assetBase": "assets", "title": "Legacy"}]
    server.book_bot.save_json(assets / "library.json", library)

    server.DIST = dist
    server.DATA = root / "data"
    server.book_bot.LIBRARY_FILE = assets / "library.json"
    server.DESKTOP_MODE = True
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.LibraryHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({"id": "legacy"}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/remove-book", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode())
        assert result["ok"] is True, result
        assert result["legacy"] is True, result
        assert result["freedBytes"] > 0, result
        assert not (assets / "book.json").exists()
        assert not (assets / "conversion.json").exists()
        assert not pages.exists()
        assert (assets / "local-pdf-cover.svg").exists()
        assert json.loads((assets / "library.json").read_text(encoding="utf-8")) == []
        print("ENDPOINT_OK", result)
    finally:
        httpd.shutdown()
        httpd.server_close()
finally:
    shutil.rmtree(root, ignore_errors=True)
print("ENDPOINT TEST PASSED")
