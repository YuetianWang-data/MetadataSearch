"""CLI and optional local web entry point for Excel metadata search."""

import argparse
import json
from pathlib import Path
import sys
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from metadata_search.loader import load_catalog
from metadata_search.query import Settings
from metadata_search.search import SearchEngine


ROOT = Path(__file__).resolve().parent
INPUT_ERRORS = (OSError, ValueError, BadZipFile, InvalidFileException, ParseError)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Excel metadata search (CLI)")
    parser.add_argument("--data-dir", type=Path, default=ROOT, help="Directory containing Excel files (non-recursive)")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json", help="Path to the JSON configuration")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--query", help="Search keywords and return JSON results")
    action.add_argument("--check", action="store_true", help="Load the catalog and return summary statistics")
    action.add_argument("--serve", action="store_true", help="Start the local search website")
    parser.add_argument("--port", type=int, default=8765, help="Web server port (default: 8765)")
    parser.add_argument("--open", action="store_true", help="Open the browser with --serve")
    parser.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    parser.add_argument("--page-size", type=int, default=20, help="Results per page, 1-100 (default: 20)")
    args = parser.parse_args()

    if args.open and not args.serve:
        parser.error("--open requires --serve")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.query is None and not args.check and not args.serve:
        parser.print_help()
        return 0
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        if args.serve:
            from metadata_search.web import serve

            serve(args.data_dir.resolve(), args.config.resolve(), args.port, args.open)
            return 0
        # Read fresh files on each invocation; no server or cached catalog is needed.
        directory = args.data_dir.resolve()
        settings = Settings.load(args.config.resolve())
        records, files = load_catalog(directory)
        if args.check:
            result = {
                "files": files, "systems": len(files), "fields": len(records),
                "data_directory": str(directory), "weights": settings.weights,
                "auto_subwords": settings.auto_subwords,
            }
        else:
            engine = SearchEngine(records, settings)
            result = engine.search(args.query, args.page, args.page_size)
    except INPUT_ERRORS as error:
        # Report expected input errors without hiding programming errors.
        parser.exit(1, f"Error: {error}\n")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
