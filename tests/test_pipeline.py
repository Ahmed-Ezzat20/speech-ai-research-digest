import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402
import update  # noqa: E402


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "data" / "data.json").read_text(encoding="utf-8"))
        cls.sources = json.loads((ROOT / "task" / "sources.json").read_text(encoding="utf-8"))

    def test_invalid_urls_are_rejected(self):
        for value in ("file:///tmp/x", "http://localhost/x", "https://127.0.0.1/x", "https://example.org/x user"):
            with self.subTest(value=value):
                with self.assertRaises(update.UpdateError):
                    update.valid_url(value, "source_url")

    def test_conflicting_duplicate_event_ids_are_rejected(self):
        checked = datetime.now(timezone.utc).replace(microsecond=0)
        event = copy.deepcopy(self.data["events"][0])
        other = copy.deepcopy(event)
        other["summary"] = other["summary"] + " conflicting payload"
        payload = {
            "run": {
                "id": "duplicate-test-run",
                "checked_at": checked.isoformat().replace("+00:00", "Z"),
                "status": "setup",
                "headline": "test",
                "event_ids": [],
                "sources_checked": [],
                "failures": [],
                "url": "digests/duplicate-test-run.html",
            },
            "events": [event, other],
            "actions": [],
            "brief": {},
            "source_state": {},
        }
        with self.assertRaises(update.UpdateError):
            update.ingest(payload, validate_only=True)

    def test_quiet_failure_is_rejected(self):
        checked = datetime.now(timezone.utc).replace(microsecond=0)
        source_ids = [s["id"] for s in self.sources["sources"]]
        payload = {
            "run": {
                "id": "quiet-failure-test",
                "checked_at": checked.isoformat().replace("+00:00", "Z"),
                "status": "quiet",
                "headline": "not actually quiet",
                "event_ids": [],
                "sources_checked": source_ids,
                "failures": ["whisper unavailable"],
                "url": "digests/quiet-failure-test.html",
            },
            "events": [],
            "actions": [],
            "brief": {},
            "source_state": {"whisper": {"status": "failed", "cursor": None}},
        }
        with self.assertRaises(update.UpdateError):
            update.ingest(payload, validate_only=True)

    def test_weekly_counts_derive_from_actual_event_dates(self):
        checked = datetime.now(timezone.utc).replace(microsecond=0)
        data = copy.deepcopy(self.data)
        event = {
            "id": "reviewed-count-test",
            "project_id": "whisper",
            "timestamp": (checked - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            "title": "Reviewed test event",
            "type": "code",
            "category": "ASR",
            "summary": "A test-only reviewed event.",
            "source_url": "https://github.com/openai/whisper",
            "significance": "routine",
            "provenance": "reviewed",
            "action": None,
        }
        data["events"].append(event)
        brief = update.rebuild_brief(data, {}, checked)
        self.assertEqual(brief["counts"]["whisper"], 1)
        self.assertEqual(brief["event_ids"], ["reviewed-count-test"])

    def test_archive_and_feed_use_real_absolute_links(self):
        archive = build.render_archive(self.data)
        atom = build.feed(self.data)
        self.assertIn("digests/setup-import-", archive)
        self.assertIn("briefs/brief-", archive)
        self.assertIn("<feed xmlns=\"http://www.w3.org/2005/Atom\">", atom)
        self.assertIn("https://ahmed-ezzat20.github.io/speech-ai-research-digest/briefs/", atom)
        self.assertNotIn("digests/setup-import-", atom)
        self.assertEqual(atom.count("<entry>"), 1)  # the genuine setup snapshot
        for token in build.TOKEN_NAMES:
            self.assertNotIn(token, (ROOT / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
