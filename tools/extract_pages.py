from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("pages", nargs="+", type=int, help="One-based PDF page numbers")
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--quality", type=int, default=78)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(args.pdf)

    for page_number in args.pages:
        page = reader.pages[page_number - 1]
        images = list(page.images)
        if not images:
            raise RuntimeError(f"Page {page_number} contains no extractable image")

        image = max(images, key=lambda item: item.image.width * item.image.height).image
        if image.width > args.width:
            height = round(image.height * args.width / image.width)
            image = image.resize((args.width, height))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")

        target = args.output / f"page-{page_number}.webp"
        image.save(target, "WEBP", quality=args.quality, method=6)
        print(f"page {page_number}: {image.width}x{image.height} -> {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
