"""CLI: PDF -> data/<paper_id>/{source.pdf, figures/*, paper.json, rag.*}."""
import argparse
import json
import shutil
import sys
from pathlib import Path

from config import DATA_DIR, PDF_EXTRACT_MODE
from extract import extract_pdf
from toc import build_toc
from pipeline import summarize_pages
from rag import build_index


def run(pdf_path: str, force: bool = False, extract_mode: str | None = None,
        ocr_only: bool = False) -> None:
    src = Path(pdf_path).resolve()
    if not src.exists():
        print(f"ERROR: PDF not found: {src}", file=sys.stderr)
        sys.exit(1)

    # OCR-only is defined as a pure vision-LLM transcription pass.
    if ocr_only:
        mode = "vision"
    else:
        mode = (extract_mode or PDF_EXTRACT_MODE or "text").strip().lower()
        if mode not in ("text", "vision"):
            mode = "text"

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

    print(f"Extraction mode: {mode}{' (ocr-only)' if ocr_only else ''}")
    payload = extract_pdf(dst_pdf, data_dir, mode, ocr_only=ocr_only)
    payload["paper_id"] = paper_id
    payload["pdf_path"] = str(dst_pdf)

    if ocr_only:
        # Pure OCR: leave summaries/TOC/RAG empty so the user can add them later
        # (POST /api/summarize-pages, /api/build-rag) without re-running the vision pass.
        payload["toc"] = []
    else:
        summarize_pages(payload["pages"])
        payload["toc"] = build_toc(dst_pdf)
        build_index(payload["pages"], data_dir)

    paper_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {paper_json}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF -> paper.json via PDF libraries")
    parser.add_argument("pdf_path", help="Path to the input PDF")
    parser.add_argument("--force", action="store_true", help="Reprocess even if paper.json exists")
    parser.add_argument("--extract-mode", choices=["text", "vision"], default=None,
                        help="How to read pages into markdown (default: PDF_EXTRACT_MODE env or 'text')")
    parser.add_argument("--vision", action="store_true",
                        help="Shortcut for --extract-mode vision")
    parser.add_argument("--ocr-only", action="store_true",
                        help="Pure vision-LLM OCR: per-page markdown only, skip figures, "
                             "tables, TOC, summaries, and RAG (add summaries/RAG later in the viewer)")
    args = parser.parse_args()
    mode = "vision" if args.vision else args.extract_mode
    run(args.pdf_path, force=args.force, extract_mode=mode, ocr_only=args.ocr_only)


if __name__ == "__main__":
    main()
