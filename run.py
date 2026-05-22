"""CLI: PDF -> data/<paper_id>/{source.pdf, figures/*, paper.json, rag.*}."""
import argparse
import json
import shutil
import sys
from pathlib import Path

from config import DATA_DIR
from extract import extract_pdf
from toc import build_toc
from pipeline import summarize_pages
from rag import build_index


def run(pdf_path: str, force: bool = False) -> None:
    src = Path(pdf_path).resolve()
    if not src.exists():
        print(f"ERROR: PDF not found: {src}", file=sys.stderr)
        sys.exit(1)

    paper_id = src.stem.lower().replace(" ", "_")
    data_dir = DATA_DIR / paper_id
    paper_json = data_dir / "paper.json"

    if paper_json.exists() and not force:
        print(f"Already processed: {paper_json}\nUse --force to reprocess.")
        return

    data_dir.mkdir(parents=True, exist_ok=True)
    dst_pdf = data_dir / "source.pdf"
    if not dst_pdf.exists() or force:
        shutil.copy2(src, dst_pdf)

    payload = extract_pdf(dst_pdf, data_dir)
    summarize_pages(payload["pages"])
    payload["toc"] = build_toc(dst_pdf)
    payload["paper_id"] = paper_id
    payload["pdf_path"] = str(dst_pdf)

    build_index(payload["pages"], data_dir)

    paper_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {paper_json}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF -> paper.json via PDF libraries")
    parser.add_argument("pdf_path", help="Path to the input PDF")
    parser.add_argument("--force", action="store_true", help="Reprocess even if paper.json exists")
    args = parser.parse_args()
    run(args.pdf_path, force=args.force)


if __name__ == "__main__":
    main()
