from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

from slivin_harness.console import configure_utf8_stdio


class ConsoleEncodingTests(unittest.TestCase):
    def test_reconfigures_legacy_charmap_streams_to_utf8(self) -> None:
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        out_buffer = io.BytesIO()
        err_buffer = io.BytesIO()
        fake_stdout = io.TextIOWrapper(out_buffer, encoding="cp1251")
        fake_stderr = io.TextIOWrapper(err_buffer, encoding="cp1251")

        try:
            sys.stdout = fake_stdout
            sys.stderr = fake_stderr

            configure_utf8_stdio()

            self.assertEqual(sys.stdout.encoding.lower().replace("-", ""), "utf8")
            self.assertEqual(sys.stderr.encoding.lower().replace("-", ""), "utf8")
            self.assertTrue(sys.stdout.line_buffering)
            self.assertTrue(sys.stderr.line_buffering)
            self.assertTrue(sys.stdout.write_through)
            self.assertTrue(sys.stderr.write_through)
            print("Русский текст → UTF-8")
            sys.stdout.flush()

            self.assertEqual(
                out_buffer.getvalue().decode("utf-8").splitlines(),
                ["Русский текст → UTF-8"],
            )
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            fake_stdout.close()
            fake_stderr.close()

    def test_launchers_force_unbuffered_python(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for name in ("run", "py", "run.cmd", "py.cmd"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertIn("PYTHONUNBUFFERED", text, name)


if __name__ == "__main__":
    unittest.main()
