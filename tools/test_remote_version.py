import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="bookbot-remote-version-"))
try:
    library = root / "library"
    assets = library / "assets"
    pages = assets / "pages"
    pages.mkdir(parents=True)
    (assets / "book.json").write_text('{"id":"legacy"}', encoding="utf-8")
    (assets / "conversion.json").write_text('{"pageCount":1}', encoding="utf-8")
    (pages / "page-1.jpg").write_bytes(b"x" * 2048)
    (assets / "local-pdf-cover.svg").write_text("<svg/>", encoding="utf-8")
    (assets / "library.json").write_text(json.dumps([
        {"id": "legacy", "assetBase": "assets", "title": "Legacy"},
        {"id": "book-x", "assetBase": "books/book-x", "title": "X"},
    ]), encoding="utf-8")
    book = library / "books" / "book-x"
    book.mkdir(parents=True)
    (book / "book.json").write_text('{"id":"book-x"}', encoding="utf-8")

    data = root / "data"
    env = os.environ.copy()
    env["READER_PASSWORD"] = "test-pass"
    env["READER_USERNAME"] = "reader"
    env["BOOKBOT_PUBLISH_TOKEN"] = "test-token"
    server = root.parent / "noop"
    script = r"D:/codex/2026-10-06/d-open-handover-md-x20/reader-prototype/release/bookbot/app/server_readonly.py"
    proc = subprocess.Popen([sys.executable, script, "--host", "127.0.0.1", "--port", "3012", "--directory", str(library), "--data", str(data)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(40):
            time.sleep(0.2)
            try:
                req = urllib.request.Request("http://127.0.0.1:3012/api/version", headers={"Authorization": "Basic " + base64.b64encode(b"reader:test-pass").decode()})
                with urllib.request.urlopen(req, timeout=2) as response:
                    version = json.loads(response.read().decode())
                break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError(proc.stderr.read().decode())
        else:
            raise RuntimeError("remote test server did not start")
        req = urllib.request.Request("http://127.0.0.1:3012/api/library-info", headers={"Authorization": "Basic " + base64.b64encode(b"reader:test-pass").decode()})
        with urllib.request.urlopen(req, timeout=3) as response:
            info = json.loads(response.read().decode())
        print("REMOTE_VERSION", version)
        print("REMOTE_LIBRARY_INFO", json.dumps(info, ensure_ascii=False, indent=2))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
finally:
    shutil.rmtree(root, ignore_errors=True)
