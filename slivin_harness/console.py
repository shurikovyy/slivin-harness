from __future__ import annotations

import sys
from typing import TextIO


def _reconfigure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")


def configure_utf8_stdio() -> None:
    """Force UTF-8 console streams for cross-platform Harness output.

    Windows Git Bash can start Python with a legacy ANSI/charmap stdout
    encoding. That corrupts Cyrillic output and can crash on characters such
    as U+2192. UTF-8 is the Harness console contract.
    """
    _reconfigure_utf8(sys.stdout)
    _reconfigure_utf8(sys.stderr)
