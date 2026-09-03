import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from codex_workbench import __version__
from codex_workbench.activity import ActivityStore
from codex_workbench.desktop_entry import install_desktop_entry
from codex_workbench.git import parse_short_status
from codex_workbench.models import ChatGPTThread, Project
from codex_workbench.settings import SettingsStore, WorkbenchSettings
from codex_workbench.terminal import TerminalAdapter


class V03SupportTests(unittest.TestCase):
    def test_release_metadata_is_consistently_050(self):
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )
        readme = (root / "README.md").read_text(encoding="utf-8")
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertEqual(__version__, "0.5.0")
        self.assertEqual(metadata["project"]["version"], __version__)
        self.assertTrue(readme.startswith("# Codex Workbench v0.5.0\n"))
        self.assertIn("## 0.5.0", changelog)

    def test_settings_normalize_and_survive_malformed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{broken", encoding="utf-8")
            store = SettingsStore(path)
            self.assertEqual(store.load().preferred_terminal, "tilix")

            saved = WorkbenchSettings(
                low_usage_threshold=7,
                critical_usage_threshold=99,
                clipboard_mode="mystery",
            )
            store.save(saved)
            loaded = store.load()

        self.assertEqual(loaded.low_usage_threshold, 7)
        self.assertEqual(loaded.critical_usage_threshold, 7)
        self.assertEqual(loaded.clipboard_mode, "auto")

    def test_activity_is_bounded_and_project_filterable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ActivityStore(Path(tmp) / "activity.json", limit=10)
            for index in range(12):
                store.record(
                    "test",
                    f"Action {index}",
                    project="one" if index % 2 else "two",
                )

            all_records = store.all()
            filtered = store.recent(project="one", limit=20)

        self.assertEqual(len(all_records), 10)
        self.assertTrue(all(item.project == "one" for item in filtered))
        self.assertEqual(all_records[0].summary, "Action 11")

    def test_short_status_classification(self):
        changes = parse_short_status(
            " M modified.py\nA  added.py\n D deleted.py\n?? new.py\n"
        )
        categories = {item.path: item.category for item in changes}
        self.assertEqual(categories["modified.py"], "modified")
        self.assertEqual(categories["added.py"], "added")
        self.assertEqual(categories["deleted.py"], "deleted")
        self.assertEqual(categories["new.py"], "untracked")

    def test_structured_thread_round_trip_retains_legacy_urls(self):
        project = Project(
            "demo",
            "/tmp/demo",
            gpt_threads=["https://chatgpt.com/c/legacy"],
            chatgpt_threads=[
                ChatGPTThread(
                    "https://chatgpt.com/c/build",
                    "Build",
                    "Implementation thread",
                )
            ],
        )
        loaded = Project.from_dict(project.to_dict())

        self.assertEqual(len(loaded.thread_references), 2)
        self.assertEqual(loaded.chatgpt_threads[0].label, "Build")

    def test_terminal_builds_detached_tilix_command_without_shell(self):
        launched = []

        def fake_popen(command, **kwargs):
            launched.append((command, kwargs))
            return mock.Mock()

        with mock.patch(
            "codex_workbench.terminal.shutil.which",
            side_effect=lambda name: "/usr/bin/tilix" if name == "tilix" else None,
        ), mock.patch(
            "codex_workbench.terminal.subprocess.Popen",
            side_effect=fake_popen,
        ):
            result = TerminalAdapter().open_command(
                Path("/tmp/project"),
                ["/usr/bin/codex-start", "account-a"],
                "tilix",
                title="demo",
                detached=True,
            )

        self.assertEqual(result, 0)
        command = launched[0][0]
        self.assertEqual(command[0], "/usr/bin/tilix")
        self.assertIn("--working-directory=/tmp/project", command)
        self.assertIn(
            "--command=/usr/bin/codex-start account-a",
            command,
        )
        self.assertTrue(launched[0][1]["start_new_session"])

    def test_desktop_installer_uses_explicit_command_and_temp_data_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = install_desktop_entry(
                data_home=Path(tmp),
                executable="/opt/cwb/bin/codex-workbench",
            )
            document = result.desktop_file.read_text(encoding="utf-8")

        self.assertIn('Exec="/opt/cwb/bin/codex-workbench"', document)
        self.assertIn("Terminal=false", document)
        self.assertTrue(result.icon_file.name.endswith(".svg"))


if __name__ == "__main__":
    unittest.main()
