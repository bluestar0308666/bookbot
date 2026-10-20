import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"D:/codex/2026-10-06/d-open-handover-md-x20/reader-prototype")
import server

root = Path(tempfile.mkdtemp(prefix="bookbot-remove-test-"))
try:
    assets = root / "dist" / "assets"
    pages = assets / "pages"
    pages.mkdir(parents=True)
    (assets / "book.json").write_text('{"id":"legacy"}', encoding="utf-8")
    (assets / "conversion.json").write_text('{"pageCount":1}', encoding="utf-8")
    (pages / "page-1.jpg").write_bytes(b"x" * 1024)
    (assets / "local-pdf-cover.svg").write_text("<svg/>", encoding="utf-8")
    library_file = assets / "library.json"
    library = [{"id": "legacy", "assetBase": "assets", "title": "Legacy"}]
    server.book_bot.save_json(library_file, library)
    data = root / "data"
    result = server.remove_library_entry(library_file, data, library[0], library)
    assert result["legacy"] is True, result
    assert result["freedBytes"] > 0, result
    assert not (assets / "book.json").exists()
    assert not (assets / "conversion.json").exists()
    assert not pages.exists()
    assert (assets / "local-pdf-cover.svg").exists()
    assert json.loads(library_file.read_text(encoding="utf-8")) == []
    print("LEGACY_OK", result)

    # standard book
    books = root / "dist" / "books" / "book-abc"
    books.mkdir(parents=True)
    (books / "book.json").write_text('{"id":"book-abc"}', encoding="utf-8")
    standard = {"id": "book-abc", "assetBase": "books/book-abc", "title": "Standard"}
    server.book_bot.save_json(library_file, [standard])
    result2 = server.remove_library_entry(library_file, data, standard, [standard])
    assert result2["legacy"] is False, result2
    assert not books.exists()
    print("STANDARD_OK", result2)

    # shared legacy must refuse
    shared_library = [{"id": "legacy-a", "assetBase": "assets"}, {"id": "legacy-b", "assetBase": "assets"}]
    server.book_bot.save_json(library_file, shared_library)
    try:
        server.remove_library_entry(library_file, data, shared_library[0], shared_library)
    except server.RemovalError as error:
        assert error.status == 409, error.status
        print("SHARED_REFUSED", str(error))
    else:
        raise AssertionError("shared legacy removal was not refused")
finally:
    shutil.rmtree(root, ignore_errors=True)

print("ALL REMOVAL TESTS PASSED")
