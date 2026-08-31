from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from slivin_harness import __version__
from slivin_harness.workflow import render_workflow_markdown, workflow_snapshot

MARKDOWN_PATH = ROOT / "docs" / "WORKFLOW.md"
JSON_PATH = ROOT / "docs" / "workflow.v1.json"


def expected_outputs() -> dict[Path, str]:
    snapshot = workflow_snapshot(harness_version=__version__)
    return {
        MARKDOWN_PATH: render_workflow_markdown(harness_version=__version__),
        JSON_PATH: json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
    }


def check_outputs() -> None:
    stale: list[str] = []
    for path, expected in expected_outputs().items():
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            stale.append(path.relative_to(ROOT).as_posix())
    if stale:
        raise RuntimeError(
            "Generated workflow documentation is stale: "
            + ", ".join(stale)
            + ". Run ./py tools/render_workflow_docs.py"
        )


def write_outputs() -> None:
    for path, content in expected_outputs().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print("WORKFLOW_DOC_WRITTEN:", path.relative_to(ROOT).as_posix())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render or verify canonical Slivin Harness workflow documentation"
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        check_outputs()
        print("WORKFLOW_DOCS_SYNC_PASS")
    else:
        write_outputs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
