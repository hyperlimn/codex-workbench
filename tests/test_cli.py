import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

from codex_workbench.clipboard import ClipboardAttempt, ClipboardResult
from codex_workbench.cli import main


class CliFallbackTests(unittest.TestCase):
    def test_copy_all_prints_context_and_clear_clipboard_failure(self):
        result = SimpleNamespace(
            status=SimpleNamespace(text="PROJECT: demo\n"),
            clipboard=ClipboardResult(
                False,
                "x11",
                attempts=(ClipboardAttempt("xclip", "no display"),),
            ),
        )
        fake_app = SimpleNamespace(copy_all=lambda _name: result)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch("codex_workbench.cli._app", return_value=fake_app):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                returncode = main(["copy-all", "demo"])

        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.getvalue(), "PROJECT: demo\n")
        self.assertIn("Clipboard copy unavailable (x11)", stderr.getvalue())
        self.assertIn("Context was printed instead", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
