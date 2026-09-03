import io
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from codex_workbench.clone import (
    CloneRequest,
    CloneResult,
    DestinationConflictError,
    GitCloneService,
    infer_repository_name,
    validate_clone_request,
    validate_repository_url,
)
from codex_workbench.projects import ProjectRegistry
from codex_workbench.services import Workbench, WorkbenchError
from codex_workbench.sessions import SessionStore


def run_git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        capture_output=True,
        check=True,
    )


class InstantProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = 130

    def kill(self):
        self.returncode = 130


class CancelAfterLaunch:
    def __init__(self):
        self.calls = 0

    def is_set(self):
        self.calls += 1
        return self.calls > 1


class RunningProcess(InstantProcess):
    def __init__(self):
        super().__init__(returncode=None)


class FakeCloneService:
    def __init__(self, *, outcome="success"):
        self.outcome = outcome
        self.calls = []

    def clone(self, request, *, cancel=None, on_progress=None):
        self.calls.append(request)
        request = validate_clone_request(request)
        if self.outcome == "failure":
            return CloneResult(
                request,
                ("git", "clone"),
                128,
                "",
                "fatal: authentication failed",
            )
        if self.outcome == "cancelled":
            return CloneResult(
                request,
                ("git", "clone"),
                130,
                "",
                "cancelled",
                cancelled=True,
            )
        destination = request.destination
        destination.mkdir()
        run_git(destination, "init", "-q", "-b", "main")
        run_git(destination, "config", "user.name", "Clone User")
        run_git(destination, "config", "user.email", "clone@example.com")
        (destination / "README.md").write_text("demo\n", encoding="utf-8")
        run_git(destination, "add", "README.md")
        run_git(destination, "commit", "-q", "-m", "initial")
        run_git(
            destination,
            "remote",
            "add",
            "origin",
            request.repository_url,
        )
        return CloneResult(request, ("git", "clone"), 0, "done\n", "")


class FailingRegistry(ProjectRegistry):
    def save(self, project):
        raise OSError("registry is read-only")


class CloneOrchestrationTests(unittest.TestCase):
    def test_real_local_git_clone_accepts_safely_claimed_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            source.mkdir()
            run_git(source, "init", "-q", "-b", "main")
            run_git(source, "config", "user.name", "Local Clone")
            run_git(source, "config", "user.email", "clone@example.com")
            (source / "README.md").write_text("local\n", encoding="utf-8")
            run_git(source, "add", "README.md")
            run_git(source, "commit", "-q", "-m", "initial")
            parent = base / "clones"
            parent.mkdir()

            result = GitCloneService(git_executable="git").clone(
                CloneRequest(str(source), parent, "copy")
            )

            self.assertTrue(result.succeeded, result.summary)
            self.assertTrue((parent / "copy" / ".git").is_dir())

    def test_infers_https_ssh_and_scp_repository_names(self):
        self.assertEqual(
            infer_repository_name(
                "https://github.example/example-org/sample-repo.git"
            ),
            "sample-repo",
        )
        self.assertEqual(
            infer_repository_name("ssh://git@example.com/acme/demo.git/"),
            "demo",
        )
        self.assertEqual(
            infer_repository_name("git@github.com:acme/widget.git"),
            "widget",
        )

    def test_url_validation_is_generic_and_rejects_command_whitespace(self):
        self.assertEqual(
            validate_repository_url("git@example.com:acme/demo.git"),
            "git@example.com:acme/demo.git",
        )
        with self.assertRaises(ValueError):
            validate_repository_url(
                "https://github.com/acme/demo.git --upload-pack=evil"
            )
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "repo with spaces"
            local.mkdir()
            self.assertEqual(
                validate_repository_url(str(local)),
                str(local.resolve()),
            )

    def test_git_cli_is_argv_only_and_captures_failure(self):
        launched = []
        progress = []

        def popen(command, **kwargs):
            launched.append((command, kwargs))
            return InstantProcess(
                128,
                stderr=(
                    "Receiving objects: 10%\r"
                    "Receiving objects: 20%\r"
                    "fatal: repository not found\n"
                ),
            )

        with tempfile.TemporaryDirectory() as tmp:
            request = CloneRequest(
                "https://example.com/acme/demo.git",
                Path(tmp),
                "demo;not-a-command",
            )
            result = GitCloneService(
                git_executable="/usr/bin/git",
                popen=popen,
            ).clone(request, on_progress=progress.append)
            destination_exists_after_failure = request.destination.exists()

        self.assertFalse(result.succeeded)
        self.assertIn("repository not found", result.stderr)
        self.assertTrue(
            any("Receiving objects: 10%" in item.message for item in progress)
        )
        self.assertEqual(
            launched[0][0][-2:],
            [
                "https://example.com/acme/demo.git",
                str(Path(tmp) / "demo;not-a-command"),
            ],
        )
        self.assertNotIn("shell", launched[0][1])
        self.assertEqual(launched[0][1]["stdin"], subprocess.DEVNULL)
        self.assertEqual(
            launched[0][1]["env"]["GIT_TERMINAL_PROMPT"],
            "0",
        )
        self.assertTrue(result.cleaned_partial)
        self.assertFalse(destination_exists_after_failure)

    def test_destination_conflict_blocks_before_git_starts(self):
        called = []
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "demo"
            destination.mkdir()
            (destination / "keep.txt").write_text("keep", encoding="utf-8")
            service = GitCloneService(
                git_executable="/usr/bin/git",
                popen=lambda *_args, **_kwargs: called.append(True),
            )
            with self.assertRaises(DestinationConflictError):
                service.clone(
                    CloneRequest(
                        "https://example.com/acme/demo.git",
                        Path(tmp),
                        "demo",
                    )
                )
        self.assertEqual(called, [])

    def test_cancelled_before_start_never_launches_git(self):
        called = []
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as tmp:
            result = GitCloneService(
                git_executable="/usr/bin/git",
                popen=lambda *_args, **_kwargs: called.append(True),
            ).clone(
                CloneRequest(
                    "https://example.com/acme/demo.git",
                    Path(tmp),
                    "demo",
                ),
                cancel=cancel,
            )
        self.assertTrue(result.cancelled)
        self.assertEqual(called, [])

    def test_running_clone_cancellation_terminates_and_cleans_new_destination(self):
        process = RunningProcess()
        with tempfile.TemporaryDirectory() as tmp:
            request = CloneRequest(
                "https://example.com/acme/demo.git",
                Path(tmp),
                "demo",
            )
            result = GitCloneService(
                git_executable="/usr/bin/git",
                popen=lambda *_args, **_kwargs: process,
            ).clone(
                request,
                cancel=CancelAfterLaunch(),
            )
            destination_exists_after_cancel = request.destination.exists()

        self.assertTrue(result.cancelled)
        self.assertEqual(result.returncode, 130)
        self.assertTrue(result.cleaned_partial)
        self.assertFalse(destination_exists_after_cancel)

    def test_broken_progress_observer_cannot_abort_cleanup(self):
        def broken_progress(_item):
            raise RuntimeError("dialog already closed")

        with tempfile.TemporaryDirectory() as tmp:
            request = CloneRequest(
                "https://example.com/acme/demo.git",
                Path(tmp),
                "demo",
            )
            result = GitCloneService(
                git_executable="/usr/bin/git",
                popen=lambda *_args, **_kwargs: InstantProcess(
                    128,
                    stderr="fatal: unavailable\n",
                ),
            ).clone(
                request,
                on_progress=broken_progress,
            )
            destination_exists_after_failure = request.destination.exists()

        self.assertFalse(result.succeeded)
        self.assertTrue(result.cleaned_partial)
        self.assertFalse(destination_exists_after_failure)

    def test_registration_happens_only_after_success_and_detects_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            clone = FakeCloneService()
            app = Workbench(
                projects=ProjectRegistry(base / "config" / "projects.json"),
                sessions=SessionStore(base / "data" / "sessions"),
                clone=clone,
            )
            result = app.clone_project(
                "demo",
                "https://example.com/acme/demo.git",
                base,
            )

            stored = app.project("demo")

        self.assertTrue(result.registered)
        self.assertEqual(stored.path.name, "demo")
        self.assertTrue(result.registration.git.is_repository)
        self.assertEqual(result.registration.git.branch, "main")
        self.assertEqual(
            result.registration.git.remote_url,
            "https://example.com/acme/demo.git",
        )
        self.assertEqual(result.registration.git.user_name, "Clone User")
        self.assertTrue(result.registration.git.clean)

    def test_failure_and_cancellation_leave_no_registration(self):
        for outcome in ("failure", "cancelled"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                app = Workbench(
                    projects=ProjectRegistry(base / "projects.json"),
                    sessions=SessionStore(base / "sessions"),
                    clone=FakeCloneService(outcome=outcome),
                )
                result = app.clone_project(
                    "demo",
                    "https://example.com/acme/demo.git",
                    base,
                )
                self.assertFalse(result.registered)
                self.assertEqual(app.projects.all(), {})

    def test_registration_failure_leaves_successful_clone_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = Workbench(
                projects=FailingRegistry(base / "projects.json"),
                sessions=SessionStore(base / "sessions"),
                clone=FakeCloneService(),
            )
            with self.assertRaisesRegex(
                WorkbenchError,
                "Clone succeeded.*Files were left untouched",
            ):
                app.clone_project(
                    "demo",
                    "https://example.com/acme/demo.git",
                    base,
                )
            self.assertTrue((base / "demo" / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
