import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from codex_workbench.clipboard import select_clipboard_backend
from codex_workbench.desktop import DesktopAdapter
from codex_workbench.gui.chooser import GtkChooserBackend
from codex_workbench.gui import terminal as gui_terminal
from codex_workbench.gui.terminal import VteTerminalBackend
from codex_workbench.platform import (
    LinuxPlatformBackend,
    UnsupportedPlatformBackend,
    UnsupportedPlatformError,
    select_platform_backend,
)
from codex_workbench.projects import ProjectRegistry
from codex_workbench.services import Workbench
from codex_workbench.sessions import SessionStore
from codex_workbench.settings import WorkbenchSettings
from codex_workbench.terminal import (
    TerminalAdapter,
    embedded_codex_environment,
    embedded_shell_spec,
    select_shell_backend,
    shell_requires_rebind,
)


class FakeShellPlatform:
    name = "test"

    def normalize_path(self, value):
        return Path(value).resolve(strict=False)

    def shell_argv(self, environ=None):
        return ["/bin/bash", "--noprofile"]


class PlatformAndTerminalTests(unittest.TestCase):
    def test_gtk_chooser_backend_keeps_folder_creation_injected(self):
        created = {}
        marker = object()

        def factory(**properties):
            created.update(properties)
            return marker

        chooser = GtkChooserBackend(folder_factory=factory)
        result = chooser.folder_dialog(
            title="Choose root",
            accept_label="Choose",
        )

        self.assertIs(result, marker)
        self.assertTrue(created["modal"])
        self.assertEqual(created["title"], "Choose root")

    def test_backend_selection_and_capabilities_are_explicit(self):
        installed = {
            "git": "/usr/bin/git",
            "gh": "/usr/bin/gh",
            "tilix": "/usr/bin/tilix",
            "xdg-open": "/usr/bin/xdg-open",
        }
        linux = select_platform_backend(
            "linux",
            which=installed.get,
        )
        capabilities = linux.capabilities()

        self.assertIsInstance(linux, LinuxPlatformBackend)
        self.assertTrue(capabilities.supported)
        self.assertTrue(capabilities.git)
        self.assertTrue(capabilities.github_cli)
        self.assertTrue(capabilities.external_terminal)
        self.assertTrue(capabilities.open_folder)
        self.assertFalse(capabilities.embedded_terminal)

    def test_unsupported_platform_is_graceful(self):
        backend = select_platform_backend("win32")
        capabilities = backend.capabilities()
        clipboard = select_clipboard_backend(backend).copy("context")

        self.assertIsInstance(backend, UnsupportedPlatformBackend)
        self.assertFalse(capabilities.supported)
        self.assertIn("Linux is the supported runtime", capabilities.detail)
        self.assertFalse(clipboard.copied)
        self.assertEqual(clipboard.session_type, "unsupported:win32")
        with self.assertRaises(UnsupportedPlatformError):
            backend.open_folder_argv(Path("C:/work"))

    def test_unsupported_workbench_does_not_rediscover_host_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = Workbench(
                projects=ProjectRegistry(base / "projects.json"),
                sessions=SessionStore(base / "sessions"),
                platform=UnsupportedPlatformBackend("darwin"),
            )
            result = app.clone_project(
                "demo",
                "https://example.com/acme/demo.git",
                base,
            )

        self.assertFalse(app.clone_service.available)
        self.assertFalse(result.registered)
        self.assertEqual(result.clone.returncode, 127)
        self.assertEqual(app.projects.all(), {})

    def test_linux_path_and_open_folder_use_argv(self):
        launched = []
        backend = LinuxPlatformBackend(
            which=lambda name: (
                "/usr/bin/xdg-open" if name == "xdg-open" else None
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "folder;not-a-command"
            path.mkdir()
            adapter = DesktopAdapter(
                platform=backend,
                launcher=lambda command, **kwargs: launched.append(
                    (command, kwargs)
                ),
            )
            result = adapter.open_folder(path)

        self.assertTrue(result.opened)
        self.assertEqual(
            launched[0][0],
            ["/usr/bin/xdg-open", str(path)],
        )
        self.assertNotIn("shell", launched[0][1])

    def test_linux_shell_resolves_relative_shell_from_path(self):
        backend = LinuxPlatformBackend(
            which=lambda name: "/usr/bin/bash" if name == "bash" else None
        )
        self.assertEqual(
            backend.shell_argv({"SHELL": "bash"}),
            ["/usr/bin/bash"],
        )

    def test_shell_backend_fallback_and_rebind_policy(self):
        fallback = select_shell_backend(
            "embedded",
            embedded_available=False,
            external_available=True,
        )
        unavailable = select_shell_backend(
            "embedded",
            embedded_available=False,
            external_available=False,
        )

        self.assertEqual(fallback.selected, "external")
        self.assertIn("unavailable", fallback.fallback_reason)
        self.assertEqual(unavailable.selected, "unavailable")
        self.assertTrue(shell_requires_rebind("one", "two"))
        self.assertFalse(shell_requires_rebind("one", "one"))

    def test_embedded_shell_spec_binds_cwd_and_argv_without_shell_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "project;touch-never-runs"
            cwd.mkdir()
            spec = embedded_shell_spec(
                cwd,
                platform=FakeShellPlatform(),
                environ={"SHELL": "/bin/ignored"},
            )

        self.assertEqual(spec.cwd, cwd.resolve())
        self.assertEqual(spec.argv, ("/bin/bash", "--noprofile"))
        self.assertNotIn(str(cwd), spec.argv)

    def test_embedded_codex_environment_prevents_nested_launcher_host(self):
        environment = embedded_codex_environment(
            {"PATH": "/usr/bin", "DISPLAY": ":1"}
        )

        self.assertIn("PATH=/usr/bin", environment)
        self.assertIn("DISPLAY=:1", environment)
        self.assertIn("CODEX_START_HOSTED=1", environment)

    def test_external_terminal_path_is_a_cwd_argument_not_a_command(self):
        launched = []
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "project;touch-never-runs"
            cwd.mkdir()
            platform = LinuxPlatformBackend(
                which=lambda name: (
                    "/usr/bin/tilix" if name == "tilix" else None
                )
            )
            adapter = TerminalAdapter(platform=platform)
            with mock.patch(
                "codex_workbench.terminal.subprocess.Popen",
                side_effect=lambda command, **kwargs: launched.append(
                    (command, kwargs)
                ),
            ):
                adapter.open_shell(cwd, "tilix", detached=True)

        command = launched[0][0]
        self.assertEqual(command[0], "/usr/bin/tilix")
        self.assertIn(f"--working-directory={cwd}", command)
        self.assertNotIn("shell", launched[0][1])

    def test_missing_vte_disables_embedded_backend_with_clear_reason(self):
        with mock.patch("codex_workbench.gui.terminal.Vte", None):
            backend = VteTerminalBackend()
            self.assertFalse(backend.available)
            self.assertIn("gir1.2-vte-3.91", backend.unavailable_reason)

    def test_vte_backend_passes_cwd_and_argv_as_separate_spawn_values(self):
        class FakeTerminal:
            def __init__(self):
                self.spawn_args = ()

            def set_hexpand(self, _value):
                pass

            def set_vexpand(self, _value):
                pass

            def set_scrollback_lines(self, _value):
                pass

            def connect(self, *_args):
                pass

            def spawn_async(self, *args):
                self.spawn_args = args

        terminal = FakeTerminal()
        fake_vte = SimpleNamespace(
            Terminal=lambda: terminal,
            PtyFlags=SimpleNamespace(DEFAULT=object()),
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            gui_terminal,
            "Vte",
            fake_vte,
        ):
            spec = embedded_shell_spec(
                Path(tmp),
                platform=FakeShellPlatform(),
            )
            session = VteTerminalBackend().create(spec)

        self.assertIs(session.terminal, terminal)
        self.assertEqual(terminal.spawn_args[1], str(spec.cwd))
        self.assertEqual(terminal.spawn_args[2], list(spec.argv))
        self.assertNotIn(str(spec.cwd), terminal.spawn_args[2])

    def test_terminal_settings_migrate_legacy_external_and_default_new_embedded(self):
        legacy = WorkbenchSettings.from_dict(
            {"version": 1, "preferred_terminal": "tilix"}
        )
        configured = WorkbenchSettings.from_dict(
            {"version": 2, "shell_mode": "embedded"}
        )

        self.assertEqual(legacy.shell_mode, "external")
        self.assertEqual(configured.shell_mode, "embedded")
        self.assertEqual(WorkbenchSettings().shell_mode, "embedded")


if __name__ == "__main__":
    unittest.main()
