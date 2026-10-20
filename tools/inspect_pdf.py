from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pypdf import PdfReader


def flatten_outline(reader: PdfReader) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []

    def visit(items: list[object], depth: int = 0) -> None:
        for item in items:
            if isinstance(item, list):
                visit(item, depth + 1)
                continue

            try:
                page_index = reader.get_destination_page_number(item)
            except Exception:
                page_index = None

            flattened.append(
                {
                    "title": getattr(item, "title", str(item)),
                    "pageIndex": page_index,
                    "pageNumber": page_index + 1 if page_index is not None else None,
                    "depth": depth,
                }
            )

    outline = reader.outline
    if isinstance(outline, list):
        visit(outline)
    return flattened


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = args.pdf
    reader = PdfReader(pdf_path)
    result = {
        "path": str(pdf_path),
        "sizeBytes": pdf_path.stat().st_size,
        "pageCount": len(reader.pages),
        "metadata": {str(key): str(value) for key, value in (reader.metadata or {}).items()},
        "outline": flatten_outline(reader),
        "sampleTextCharacters": {
            "first": len((reader.pages[0].extract_text() or "").strip()),
            "twentieth": len((reader.pages[min(19, len(reader.pages) - 1)].extract_text() or "").strip()),
        },
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(args.output)
    else:
        print(payload)


if __name__ == "__main__":
    main()
