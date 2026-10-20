from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a scanned PDF into streamable JPEG pages.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--quality", type=int, default=78)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    return parser.parse_args()


def placeholder(width: int, page_number: int, error: Exception) -> Image.Image:
    height = round(width * 1.391)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((70, 80), f"Page {page_number} could not be extracted", fill="#333333")
    draw.text((70, 120), str(error)[:180], fill="#777777")
    return image


def extract_page(reader: PdfReader, page_number: int, width: int) -> Image.Image:
    page = reader.pages[page_number - 1]
    images = list(page.images)
    if not images:
        raise RuntimeError("No extractable image on this scanned page")

    image = max(images, key=lambda item: item.image.width * item.image.height).image
    if image.mode != "RGB":
        image = image.convert("RGB")
    if image.width > width:
        height = round(image.height * width / image.width)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return image


def render_fallback_pages(pdf: Path, output: Path, pages: list[int], width: int, quality: int) -> dict[int, str]:
    if not pages:
        return {}
    script = Path(__file__).resolve().parent / "render_pdf_pages.ps1"
    powershell = shutil.which("powershell.exe") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    temp_dir = output.parent / ".pdf-render-fallback"
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    command = [
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
        "-PdfPath", str(pdf), "-OutputDir", str(temp_dir),
        "-Pages", ",".join(str(page) for page in pages), "-Width", str(width),
    ]
    failures: dict[int, str] = {}
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    rendered = 0
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        if process.stdout:
            for line in process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                print(line, flush=True)
                if line.startswith("rendered page"):
                    rendered += 1
                    print(f"fallback={rendered}/{len(pages)}", flush=True)
        process.wait(timeout=min(1800, max(120, len(pages) * 15)))
    except Exception as error:
        try:
            process.kill()
        except Exception:
            pass
        return {page: f"Windows PDF renderer failed: {error}" for page in pages}

    for page in pages:
        source = temp_dir / f"page-{page}.png"
        target = output / f"page-{page}.jpg"
        try:
            with Image.open(source) as opened:
                image = opened.convert("RGB")
                if image.width > width:
                    height = round(image.height * width / image.width)
                    image = image.resize((width, height), Image.Resampling.LANCZOS)
                image.save(target, "JPEG", quality=quality, optimize=True, progressive=True, subsampling=1)
        except Exception as error:
            failures[page] = str(error)
    shutil.rmtree(temp_dir, ignore_errors=True)
    return failures


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    metadata_reader = PdfReader(args.pdf)
    page_count = len(metadata_reader.pages)
    end = min(args.end or page_count, page_count)
    start_time = time.perf_counter()
    errors: list[dict[str, object]] = []
    converted = 0
    skipped = 0
    prior_errors: set[int] = set()
    if args.resume:
        conversion_file = args.output.parent / "conversion.json"
        try:
            previous = json.loads(conversion_file.read_text(encoding="utf-8"))
            for item in previous.get("errorPages", []):
                try:
                    prior_errors.add(int(item.get("page")))
                except (TypeError, ValueError):
                    pass
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
    thread_state = threading.local()

    def thread_reader() -> PdfReader:
        if not hasattr(thread_state, "reader"):
            thread_state.reader = PdfReader(args.pdf)
        return thread_state.reader

    def convert_one(page_number: int) -> tuple[int, str | None]:
        target = args.output / f"page-{page_number}.jpg"
        try:
            image = extract_page(thread_reader(), page_number, args.width)
        except Exception as error:
            return page_number, str(error)
        image.save(
            target,
            "JPEG",
            quality=args.quality,
            optimize=True,
            progressive=True,
            subsampling=1,
        )
        return page_number, None

    pending: list[int] = []
    fallback_pages: list[int] = []
    extract_errors: dict[int, str] = {}

    for page_number in range(max(args.start, 1), end + 1):
        target = args.output / f"page-{page_number}.jpg"
        if args.resume and target.exists() and target.stat().st_size > 0 and page_number not in prior_errors:
            skipped += 1
            continue
        pending.append(page_number)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(convert_one, page): page for page in pending}
        for future in concurrent.futures.as_completed(futures):
            page_number, error_text = future.result()
            converted += 1
            if error_text:
                extract_errors[page_number] = error_text
                fallback_pages.append(page_number)
            if converted % 25 == 0 or converted == len(pending):
                elapsed = time.perf_counter() - start_time
                print(
                    f"progress converted={converted}/{len(pending)}; last_page={page_number}; "
                    f"skipped={skipped}; elapsed={elapsed:.1f}s",
                    flush=True,
                )

    fallback_failures: dict[int, str] = {}
    if fallback_pages:
        print(f"fallback rendering {len(fallback_pages)} pages with Windows PDF renderer", flush=True)
        fallback_failures = render_fallback_pages(args.pdf, args.output, sorted(fallback_pages), args.width, args.quality)
        for page_number in sorted(fallback_pages):
            if page_number in fallback_failures:
                detail = f"{extract_errors.get(page_number, '')}; fallback render failed: {fallback_failures[page_number]}"
                image = placeholder(args.width, page_number, RuntimeError(detail))
                image.save(
                    args.output / f"page-{page_number}.jpg",
                    "JPEG",
                    quality=args.quality,
                    optimize=True,
                    progressive=True,
                    subsampling=1,
                )
                errors.append({"page": page_number, "error": detail})

    files = [path for path in args.output.glob("page-*.jpg") if path.stat().st_size > 0]
    total_bytes = sum(path.stat().st_size for path in files)
    result = {
        "sourceBytes": args.pdf.stat().st_size,
        "pageCount": page_count,
        "convertedFiles": len(files),
        "outputBytes": total_bytes,
        "averagePageBytes": round(total_bytes / len(files)) if files else 0,
        "width": args.width,
        "quality": args.quality,
        "elapsedSeconds": round(time.perf_counter() - start_time, 1),
        "fallbackPages": len(fallback_pages),
        "fallbackSucceeded": len(fallback_pages) - len(fallback_failures),
        "errorPages": errors,
    }
    (args.output.parent / "conversion.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
