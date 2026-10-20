# BookBot

BookBot is a private, self-hosted reading workspace for very large PDF books. It combines a local Windows reader with an optional server library, so the same book can be opened quickly on a phone, tablet or another computer.

## What it does

- opens local PDFs directly through PDF.js
- converts large PDFs into resumable, page-by-page web books
- provides continuous scrolling, split view, outline navigation and multiple open books
- saves progress, reading time, bookmarks, highlights and notes
- publishes processed books to a private server in chunks
- installs as a PWA on supported browsers

BookBot does not include AI and does not upload a PDF unless you explicitly publish it.

## Windows quick start

1. Install Python 3.11 or newer.
2. Create an environment and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\python -m pip install -r requirements.txt
   ```

3. Double-click `启动BookBot桌面版.cmd`, or run:

   ```powershell
   .\.venv\Scripts\python server.py --desktop
   ```

The local reader uses `http://127.0.0.1:4173/` by default. Local paths and reading data stay under `data/`, which is excluded from Git.

## Process a large PDF

Put the PDF in `待处理书籍/`, then run:

```powershell
.\.venv\Scripts\python tools\book_bot.py
```

The generated pages are written below `dist/books/`. Conversion can resume after interruption. Large PDFs need additional disk space; keep the original file until you have verified the generated book.

## Private server deployment

The included Linux service listens on `127.0.0.1:13011`. Put an authenticated HTTPS reverse proxy in front of it; do not expose the service directly.

For an upgrade of an existing installation that already has `/opt/bookbot/library/assets/library.json`:

```bash
sudo env BOOKBOT_USER="$USER" bash app/install.sh
```

The installer preserves `library/books/`, the library index, converted pages and `data/reader.db`. It also creates a database backup before restarting the service.

For a new deployment, first create `/opt/bookbot/library/assets/library.json` containing `[]`, copy this repository to the server, create `/etc/bookbot.env` from `deploy/bookbot.env.example`, and then run the installer. See `deploy/Caddyfile.example` for a reverse-proxy pattern.

## Publishing from Windows

Copy `data.example/publish-config.example.json` to `data/publish-config.json`, set your server URL and keep the token private. You can set the token interactively:

```powershell
$env:BOOKBOT_SERVER_BASE = "https://books.example.com"
.\tools\set_publish_token_interactive.ps1
```

## Data layout

- `data/`: reading profile, sync records and publishing configuration
- `library/books/`: server-side books and pages
- `library/assets/library.json`: server bookshelf index
- `dist/books/`: locally converted books

All four contain personal or copyrighted material and are ignored by Git.

## Tests

```powershell
.\.venv\Scripts\python tools\test_legacy_removal.py
.\.venv\Scripts\python tools\test_remove_endpoint.py
.\.venv\Scripts\python tools\test_local_only_removal.py
.\.venv\Scripts\python tools\test_local_plus_stream_removal.py
.\.venv\Scripts\python tools\test_remote_version.py
.\.venv\Scripts\python tools\test_publish_e2e.py
.\.venv\Scripts\python tools\test_publish_client.py
node --check library\app.js
```

## License and third-party code

BookBot source is licensed under MIT. The vendored PDF.js distribution and fonts retain their own license files under `library/vendor/pdfjs/`. No books or demonstration PDFs are included.
