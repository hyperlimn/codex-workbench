import subprocess
import unittest

from codex_workbench.clipboard import ClipboardService, detect_desktop_session


class ClipboardTests(unittest.TestCase):
    @staticmethod
    def installed(_name):
        return "/fake/helper"

    def test_explicit_x11_never_uses_wl_copy(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        service = ClipboardService(
            environ={
                "XDG_SESSION_TYPE": "x11",
                "DISPLAY": ":0",
                # A stale value must not override the declared desktop.
                "WAYLAND_DISPLAY": "wayland-0",
            },
            which=self.installed,
            runner=runner,
        )
        result = service.copy("context")

        self.assertTrue(result.copied)
        self.assertEqual(result.helper, "xclip")
        self.assertEqual(commands, [["xclip", "-selection", "clipboard"]])

    def test_wayland_failure_falls_back_to_xwayland(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            if command[0] == "wl-copy":
                return subprocess.CompletedProcess(
                    command, 1, "", "Failed to connect to Wayland"
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        result = ClipboardService(
            environ={
                "XDG_SESSION_TYPE": "wayland",
                "WAYLAND_DISPLAY": "wayland-0",
                "DISPLAY": ":0",
            },
            which=self.installed,
            runner=runner,
        ).copy("context")

        self.assertTrue(result.copied)
        self.assertEqual(result.helper, "xclip")
        self.assertEqual([item[0] for item in commands], ["wl-copy", "xclip"])
        self.assertIn("wl-copy", result.attempts[0].helper)

    def test_failed_helper_is_reported_without_exception(self):
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1, "", "no display")

        result = ClipboardService(
            environ={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":2"},
            which=lambda name: "/fake/xclip" if name == "xclip" else None,
            runner=runner,
        ).copy("context")

        self.assertFalse(result.copied)
        self.assertIn("no display", result.error_summary)

    def test_unexpected_runner_error_is_still_nonfatal(self):
        def runner(_command, **_kwargs):
            raise RuntimeError("clipboard backend crashed")

        result = ClipboardService(
            environ={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":2"},
            which=self.installed,
            runner=runner,
        ).copy("context")

        self.assertFalse(result.copied)
        self.assertIn("backend crashed", result.error_summary)

    def test_no_display_means_no_clipboard_attempt(self):
        called = False

        def runner(_command, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("runner should not be called")

        result = ClipboardService(
            environ={"XDG_SESSION_TYPE": "tty"},
            which=self.installed,
            runner=runner,
        ).copy("context")

        self.assertFalse(result.copied)
        self.assertFalse(called)
        self.assertEqual(detect_desktop_session({"XDG_SESSION_TYPE": "tty"}), "tty")


if __name__ == "__main__":
    unittest.main()
