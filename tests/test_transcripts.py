import os
import tempfile
import unittest
from pathlib import Path

from codex_workbench.models import Project, WorkSession
from codex_workbench.transcripts import ProjectTranscriptDiscovery


class TranscriptDiscoveryTests(unittest.TestCase):
    def test_suggests_likely_recent_files_without_scraping_or_deep_walk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            likely = root / "codex-export-transcript.md"
            likely.write_text("# transcript\n", encoding="utf-8")
            unrelated = root / "README.md"
            unrelated.write_text("# project\n", encoding="utf-8")
            nested = root / "notes"
            nested.mkdir()
            candidate = nested / "conversation-export.txt"
            candidate.write_text("history\n", encoding="utf-8")
            excluded = root / ".git"
            excluded.mkdir()
            (excluded / "codex-transcript.md").write_text(
                "ignore\n", encoding="utf-8"
            )
            os.utime(likely, (likely.stat().st_atime, likely.stat().st_mtime + 5))
            project = Project("demo", str(root))
            session = WorkSession("s", "catalog", "demo")

            result = ProjectTranscriptDiscovery().discover(project, session)

        paths = [item.path.name for item in result]
        self.assertEqual(paths[0], likely.name)
        self.assertIn(candidate.name, paths)
        self.assertNotIn(unrelated.name, paths)
        self.assertNotIn("codex-transcript.md", paths)
        self.assertEqual(result[0].confidence, "high")

    def test_missing_project_is_nonfatal(self):
        project = Project("missing", "/definitely/not/here")
        self.assertEqual(ProjectTranscriptDiscovery().discover(project), [])


if __name__ == "__main__":
    unittest.main()
