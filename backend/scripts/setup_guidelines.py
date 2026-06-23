"""Copy guideline PDFs into backend/guidelines and run ingestion."""

import argparse
import shutil
from pathlib import Path

from rag.ingest import ingest_directory
from dotenv import load_dotenv
load_dotenv()

def copy_pdfs(source_dir: Path, dest_dir: Path) -> list[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for pdf in source_dir.glob("**/*.pdf"):
        target = dest_dir / pdf.name
        if not target.exists():
            shutil.copy2(pdf, target)
        copied.append(pdf.name)
    return copied


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="Directory containing guideline PDFs")
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()

    source = Path(args.source)
    dest = Path(__file__).resolve().parent.parent / "guidelines"

    names = copy_pdfs(source, dest)
    print(f"Copied {len(names)} PDFs: {names}")

    result = ingest_directory(dest, clear_existing=args.clear)
    print(result)


if __name__ == "__main__":
    main()
