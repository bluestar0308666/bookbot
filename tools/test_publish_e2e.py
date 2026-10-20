"""End-to-end test for BookBot v35 publish API.

Starts release/bookbot/app/server_readonly.py on a random port against a
temporary library, then exercises plan -> chunk upload -> commit -> re-plan,
plus security checks.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "release" / "bookbot" / "app" / "server_readonly.py"
sys.path.insert(0, str(ROOT / "tools"))
import publish_manifest  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(method, url, *, bearer=None, basic=None, json_body=None, data=None):
    headers = {"Accept": "application/json"}
    body = data
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
            return response.status, (json.loads(raw.decode()) if raw else {})
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        return error.code, payload


def make_book(dist: Path, book_id: str, title: str, pages: int = 3):
    book = dist / "books" / book_id
    (book / "pages").mkdir(parents=True, exist_ok=True)
    (book / "book.json").write_text(json.dumps({
        "id": book_id, "title": title, "assetBase": f"books/{book_id}",
        "sourceName": f"{title}.pdf", "sourceBytes": 123, "pageCount": pages, "outline": [],
    }, ensure_ascii=False), encoding="utf-8")
    (book / "conversion.json").write_text(json.dumps({"pageCount": pages}), encoding="utf-8")
    for index in range(1, pages + 1):
        (book / "pages" / f"page-{index}.jpg").write_bytes(b"JPEG" + bytes([index]) * 256)
    return book


def upload_file(base, token, session, relative, path):
    size = path.stat().st_size
    offset = 0
    chunk_size = 64 * 1024
    while offset < size:
        chunk = path.read_bytes()[offset:offset + chunk_size]
        url = (f"{base}/api/publish/file?session={urllib.parse.quote(session)}"
               f"&path={urllib.parse.quote(relative, safe='')}&offset={offset}")
        status, payload = request("PUT", url, bearer=token, data=chunk)
        assert status == 200 and payload.get("ok"), (status, payload)
        offset += len(chunk)
    return offset


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="bookbot-publish-"))
    library = work / "library"
    data = work / "data"
    dist = work / "dist"
    (library / "assets").mkdir(parents=True)
    (library / "books").mkdir(parents=True)
    data.mkdir(parents=True)
    (library / "assets" / "library.json").write_text("[]", encoding="utf-8")
    book_id = "book-test123456"
    title = "测试书"
    make_book(dist, book_id, title)

    manifest, _ = publish_manifest.build_manifest(dist, book_id)
    token = "test-publish-token"
    basic = "reader:test-reader-pass"
    port = free_port()
    env = {**os.environ, "READER_PASSWORD": "test-reader-pass", "READER_USERNAME": "reader", "BOOKBOT_PUBLISH_TOKEN": token}
    process = subprocess.Popen(
        [sys.executable, str(SERVER), "--host", "127.0.0.1", "--port", str(port),
         "--directory", str(library), "--data", str(data)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                status, _payload = request("GET", f"{base}/api/status", basic=basic)
                if status == 200:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("server did not start")

        # 1) plan
        status, plan = request("POST", f"{base}/api/publish/plan", bearer=token, json_body={"manifest": manifest})
        assert status == 200 and plan.get("ok") and plan.get("canProceed"), (status, plan)
        assert plan["bookId"] == book_id
        assert len(plan["missingFiles"]) == len(manifest["files"]), plan
        session = plan["sessionId"]

        # 2) upload
        book_root = dist / "books" / book_id
        for relative in plan["missingFiles"]:
            upload_file(base, token, session, relative, book_root / relative)

        # 3) status
        status, progress = request("GET", f"{base}/api/publish/status?session={session}", bearer=token)
        assert status == 200 and progress.get("percent") == 100, (status, progress)

        # 4) commit
        status, commit = request("POST", f"{base}/api/publish/commit?session={session}", bearer=token)
        assert status == 200 and commit.get("ok"), (status, commit)
        assert (library / "books" / book_id / "book.json").is_file()
        library_json = json.loads((library / "assets" / "library.json").read_text(encoding="utf-8"))
        assert any(item.get("id") == book_id for item in library_json), library_json
        assert (library / "books" / book_id / "manifest.json").is_file()

        # 5) library snapshot
        status, snapshot = request("GET", f"{base}/api/publish/library", bearer=token)
        assert status == 200 and book_id in snapshot.get("published", {}), (status, snapshot)

        # 6) same manifest -> upToDate
        status, plan2 = request("POST", f"{base}/api/publish/plan", bearer=token, json_body={"manifest": manifest})
        assert status == 200 and plan2.get("upToDate") is True, (status, plan2)

        # 7) change one page -> only that file missing; previous version kept
        page = dist / "books" / book_id / "pages" / "page-2.jpg"
        page.write_bytes(b"CHANGED" + page.read_bytes())
        manifest2, _ = publish_manifest.build_manifest(dist, book_id)
        status, plan3 = request("POST", f"{base}/api/publish/plan", bearer=token, json_body={"manifest": manifest2})
        assert status == 200 and plan3.get("missingFiles") == ["pages/page-2.jpg"], (status, plan3)
        for relative in plan3["missingFiles"]:
            upload_file(base, token, plan3["sessionId"], relative, book_root / relative)
        status, commit2 = request("POST", f"{base}/api/publish/commit?session={plan3['sessionId']}", bearer=token)
        assert status == 200 and commit2.get("ok"), (status, commit2)
        assert (library / ".previous" / book_id / "pages" / "page-2.jpg").is_file(), "previous version missing"

        # 8) security: path traversal / pdf / bad token
        bad = json.loads(json.dumps(manifest2))
        bad["files"].append({"path": "../evil.jpg", "size": 1, "sha256": "0" * 64})
        status, payload = request("POST", f"{base}/api/publish/plan", bearer=token, json_body={"manifest": bad})
        assert status == 400, (status, payload)

        bad2 = json.loads(json.dumps(manifest2))
        bad2["files"].append({"path": "raw.pdf", "size": 1, "sha256": "0" * 64})
        status, payload = request("POST", f"{base}/api/publish/plan", bearer=token, json_body={"manifest": bad2})
        assert status == 400, (status, payload)

        status, payload = request("POST", f"{base}/api/publish/plan", bearer="wrong", json_body={"manifest": manifest2})
        assert status == 401, (status, payload)

        # 9) checksum mismatch must not replace the good version
        page3 = dist / "books" / book_id / "pages" / "page-3.jpg"
        page3.write_bytes(b"NEW" + page3.read_bytes())
        manifest3, _ = publish_manifest.build_manifest(dist, book_id)
        status, plan4 = request("POST", f"{base}/api/publish/plan", bearer=token, json_body={"manifest": manifest3})
        assert status == 200 and plan4.get("canProceed") and plan4.get("sessionId"), (status, plan4)
        for relative in plan4.get("missingFiles", []):
            upload_file(base, token, plan4["sessionId"], relative, book_root / relative)
        target = library / ".publish" / plan4["sessionId"] / "pages" / "page-3.jpg"
        assert target.exists(), "temp file missing"
        target.write_bytes(b"CORRUPTED")
        status, payload = request("POST", f"{base}/api/publish/commit?session={plan4['sessionId']}", bearer=token)
        assert status == 400, (status, payload)
        live_page3 = library / "books" / book_id / "pages" / "page-3.jpg"
        assert live_page3.is_file() and not live_page3.read_bytes().startswith(b"NEW"), "bad commit replaced live version"

        # 10) hidden temp dirs are not served
        status, _payload = request("GET", f"{base}/.previous/{book_id}/book.json", basic=basic)
        assert status == 404, status
        status, _payload = request("GET", f"{base}/.publish/x", basic=basic)
        assert status == 404, status

        print("ALL TESTS PASSED")
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
