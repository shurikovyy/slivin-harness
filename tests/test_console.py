from __future__ import annotations

import io
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
