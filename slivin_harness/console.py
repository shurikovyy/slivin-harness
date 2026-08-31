from __future__ import annotations

import sys
from typing import TextIO


def _reconfigure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(
            encoding="utf-8",
            errors="replace",
            line_buffering=True,
            write_through=True,
        )


def configure_utf8_stdio() -> None:
    """Force UTF-8, line-buffered console streams for live Harness output.

    Windows Git Bash can start Python with a legacy ANSI/charmap stdout
    encoding. That corrupts Cyrillic output and can crash on characters such
    as U+2192. Git Bash can also expose stdout as a pipe, which makes Python
    buffer ordinary ``print`` calls until process exit. UTF-8 plus line buffering
    is the Harness console contract so heartbeat/progress lines remain live.
    """
    _reconfigure_utf8(sys.stdout)
    _reconfigure_utf8(sys.stderr)
