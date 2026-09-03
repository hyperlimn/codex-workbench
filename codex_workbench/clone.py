from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO
from urllib.parse import unquote, urlparse


class CloneValidationError(ValueError):
    pass


class DestinationConflictError(CloneValidationError):
    pass


@dataclass(frozen=True)
class CloneRequest:
    repository_url: str
    destination_parent: Path
    destination_folder: str

    @property
    def destination(self) -> Path:
        return self.destination_parent / self.destination_folder


@dataclass(frozen=True)
class CloneProgress:
    message: str
    stream: str = "status"


@dataclass(frozen=True)
class CloneResult:
    request: CloneRequest
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False
    cleaned_partial: bool = False

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.cancelled

    @property
    def summary(self) -> str:
        if self.cancelled:
            return "Clone cancelled"
        if self.succeeded:
            return f"Cloned into {self.request.destination}"
        detail = (self.stderr or self.stdout).strip()
        return detail.splitlines()[-1] if detail else f"git clone exited {self.returncode}"


_SCP_URL = re.compile(
    r"^(?:[^@\s/:]+@)?[^\s/:]+:[^\s]+$"
)
_REMOTE_SCHEMES = {"git", "http", "https", "ssh"}


def validate_repository_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise CloneValidationError("Repository URL cannot be empty.")
    if any(ord(character) < 32 for character in url):
        raise CloneValidationError(
            "Repository URL cannot contain control characters."
        )
    local = Path(url).expanduser()
    if any(character.isspace() for character in url):
        if local.exists():
            return str(local.resolve(strict=False))
        raise CloneValidationError(
            "Remote repository URLs cannot contain whitespace."
        )
    parsed = urlparse(url)
    if parsed.scheme in _REMOTE_SCHEMES:
        if not parsed.netloc or not parsed.path.strip("/"):
            raise CloneValidationError("Repository URL is missing a host or repository path.")
        return url
    if parsed.scheme == "file":
        if not parsed.path:
            raise CloneValidationError("File repository URL is missing a path.")
        return url
    if parsed.scheme:
        raise CloneValidationError(
            f"Unsupported Git repository URL scheme: {parsed.scheme}"
        )
    if _SCP_URL.fullmatch(url):
        return url
    if local.exists():
        return str(local.resolve(strict=False))
    raise CloneValidationError(
        "Use an http(s), ssh, git, file, scp-style, or existing local Git URL."
    )


def infer_repository_name(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url:
        return ""
    if _SCP_URL.fullmatch(url) and "://" not in url:
        candidate = url.rsplit(":", 1)[-1].rstrip("/").rsplit("/", 1)[-1]
    else:
        parsed = urlparse(url)
        candidate = unquote(parsed.path).rstrip("/").rsplit("/", 1)[-1]
    if candidate.casefold().endswith(".git"):
        candidate = candidate[:-4]
    candidate = candidate.strip()
    return candidate if candidate not in {".", ".."} else ""


def validate_clone_request(request: CloneRequest) -> CloneRequest:
    url = validate_repository_url(request.repository_url)
    parent = request.destination_parent.expanduser().resolve(strict=False)
    if not parent.is_dir():
        raise CloneValidationError(
            f"Destination parent directory does not exist: {parent}"
        )
    folder = request.destination_folder.strip()
    if (
        not folder
        or folder in {".", ".."}
        or "/" in folder
        or "\\" in folder
        or any(ord(character) < 32 for character in folder)
    ):
        raise CloneValidationError(
            "Destination folder must be one safe folder name."
        )
    destination = parent / folder
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise DestinationConflictError(
                f"Destination already exists and is not empty: {destination}"
            )
    return CloneRequest(url, parent, folder)


class GitCloneService:
    """Cancellable Git CLI clone orchestration with no registry side effects."""

    def __init__(
        self,
        *,
        git_executable: str | None = None,
        which: Callable[[str], str | None] | None = None,
        popen: Callable[..., subprocess.Popen[str]] | None = None,
    ):
        discover = which or shutil.which
        self.git_executable = (
            discover("git") or ""
            if git_executable is None
            else git_executable
        )
        self.popen = popen or subprocess.Popen

    @property
    def available(self) -> bool:
        return bool(self.git_executable)

    @staticmethod
    def _notify(
        callback: Callable[[CloneProgress], None] | None,
        progress: CloneProgress,
    ) -> None:
        if callback is None:
            return
        try:
            callback(progress)
        except Exception:
            # Progress is observational. A closed UI or broken reporter must
            # never abort Git or change registration/cleanup semantics.
            pass

    @staticmethod
    def _read_stream(
        stream: TextIO | None,
        stream_name: str,
        output: queue.Queue[tuple[str, str]],
    ) -> None:
        if stream is None:
            return
        pending: list[str] = []
        for character in iter(lambda: stream.read(1), ""):
            pending.append(character)
            # Git progress redraws with carriage returns. Emitting at either
            # delimiter keeps GTK status live while retaining exact output.
            if character in {"\r", "\n"}:
                output.put((stream_name, "".join(pending)))
                pending.clear()
        if pending:
            output.put((stream_name, "".join(pending)))
        stream.close()

    @staticmethod
    def _cleanup_partial(destination: Path, *, existed_before: bool) -> bool:
        if existed_before or not destination.exists():
            return False
        try:
            shutil.rmtree(destination)
        except OSError:
            return False
        return True

    def clone(
        self,
        request: CloneRequest,
        *,
        cancel: threading.Event | None = None,
        on_progress: Callable[[CloneProgress], None] | None = None,
    ) -> CloneResult:
        request = validate_clone_request(request)
        cancel = cancel or threading.Event()
        destination = request.destination
        if not self.available:
            return CloneResult(
                request,
                (),
                127,
                "",
                "Git executable was not found.",
            )
        if cancel.is_set():
            return CloneResult(
                request,
                (),
                130,
                "",
                "Clone cancelled before it started.",
                cancelled=True,
            )
        created_destination = False
        if not destination.exists():
            try:
                # Claim the destination atomically before Git starts. Git can
                # clone into this empty directory, and cleanup can now never
                # remove a path that appeared in a validation/Popen race.
                destination.mkdir()
                created_destination = True
            except FileExistsError as error:
                raise DestinationConflictError(
                    f"Destination appeared before clone started: {destination}"
                ) from error
        command = (
            self.git_executable,
            "clone",
            "--progress",
            "--",
            request.repository_url,
            str(destination),
        )
        self._notify(
            on_progress,
            CloneProgress(f"Cloning into {destination}…"),
        )
        try:
            process = self.popen(
                list(command),
                cwd=request.destination_parent,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                bufsize=1,
                env={
                    **os.environ,
                    # A background GTK task cannot safely own an interactive
                    # credential prompt. Existing credential helpers and SSH
                    # agents still work; missing auth fails with captured text.
                    "GIT_TERMINAL_PROMPT": "0",
                },
            )
        except OSError as error:
            cleaned = self._cleanup_partial(
                destination,
                existed_before=not created_destination,
            )
            return CloneResult(
                request,
                command,
                127,
                "",
                str(error),
                cleaned_partial=cleaned,
            )

        messages: queue.Queue[tuple[str, str]] = queue.Queue()
        readers = [
            threading.Thread(
                target=self._read_stream,
                args=(process.stdout, "stdout", messages),
                daemon=True,
            ),
            threading.Thread(
                target=self._read_stream,
                args=(process.stderr, "stderr", messages),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

        captured: dict[str, list[str]] = {"stdout": [], "stderr": []}
        cancelled = False
        while process.poll() is None:
            if cancel.is_set():
                cancelled = True
                try:
                    process.terminate()
                except OSError:
                    # The process may have exited between poll() and cancel.
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    process.wait()
                break
            try:
                stream_name, line = messages.get(timeout=0.05)
            except queue.Empty:
                continue
            captured[stream_name].append(line)
            if line.strip():
                self._notify(
                    on_progress,
                    CloneProgress(line.rstrip(), stream_name),
                )

        for reader in readers:
            reader.join(timeout=1)
        while True:
            try:
                stream_name, line = messages.get_nowait()
            except queue.Empty:
                break
            captured[stream_name].append(line)
            if line.strip():
                self._notify(
                    on_progress,
                    CloneProgress(line.rstrip(), stream_name),
                )

        returncode = process.returncode
        if returncode is None:
            returncode = process.wait()
        cleaned = False
        if cancelled or returncode != 0:
            cleaned = self._cleanup_partial(
                destination,
                existed_before=not created_destination,
            )
        result = CloneResult(
            request,
            command,
            returncode,
            "".join(captured["stdout"]),
            "".join(captured["stderr"]),
            cancelled,
            cleaned,
        )
        self._notify(on_progress, CloneProgress(result.summary))
        return result
