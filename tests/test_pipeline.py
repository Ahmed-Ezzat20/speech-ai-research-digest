import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402
import harvest  # noqa: E402


class PublisherPipelineTests(unittest.TestCase):
    def fixture_root(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        for directory in ("scripts", "task", "data", "assets"):
            (root / directory).mkdir()
        for relative in ("scripts/harvest.py", "scripts/build.py", "task/sources.json", "task/discovery_state.json", "data/data.json", "assets/page.html", "assets/style.css", "assets/app.js"):
            shutil.copy(ROOT / relative, root / relative)
        config = json.loads((root / "task/sources.json").read_text())
        (root / "data/data.json").write_text(json.dumps(harvest.base_data(config, "2026-10-25T11:00:00Z")))
        (root / "task/discovery_state.json").write_text(json.dumps({"schema_version":1,"feeds":{}}))
        self.addCleanup(td.cleanup)
        return root

    def batch(self, partial=False):
        checked = "2026-10-25T12:00:00Z"
        feeds = []
        for feed_id, cursor in (("arxiv-speech", "2026-10-25T11:59:00Z"), ("hf-asr", "2026-10-25T11:58:00Z"), ("hf-tts", "2026-10-25T11:57:00Z")):
            failed = partial and feed_id == "hf-asr"
            feeds.append({"id": feed_id, "status": "partial" if failed else "ok", "since": "2026-10-23T12:00:00Z", "complete": not failed, "cursor": None if failed else cursor, "received_count": 1 if feed_id != "hf-tts" else 0, "error": "truncated at configured cap" if failed else None})
        return {"schema_version": 1, "checked_at": checked, "mode": "automatic_discovery", "feeds": feeds, "items": [
            {"id": "paper-a1", "asset_key": "2601.12345", "feed_id": "arxiv-speech", "kind": "paper", "revision": "v1", "timestamp": "2026-10-25T10:00:00Z", "timestamp_basis": "published", "title": "A speech paper", "summary": "Abstract supplied by arXiv.", "source_url": "https://arxiv.org/abs/2601.12345v1", "category": "Research", "metadata": {"authors": ["A"], "license": None}
            },
            {"id": "model-a1", "asset_key": "org/asr-model", "feed_id": "hf-asr", "kind": "model", "revision": "abcdef123456", "timestamp": "2026-10-25T09:00:00Z", "timestamp_basis": "repository_modified", "title": "An ASR model", "summary": "Card metadata supplied by Hugging Face.", "source_url": "https://huggingface.co/org/asr-model", "category": "ASR", "metadata": {"sha": "abcdef123456", "license": "apache-2.0"}
            }
        ]}

    def test_query_only_config_and_empty_active_ledger(self):
        config = json.loads((ROOT / "task/sources.json").read_text())
        data = json.loads((ROOT / "data/data.json").read_text())
        self.assertEqual(config["schema_version"], 2)
        self.assertEqual([f["id"] for f in config["discovery"]["feeds"]], ["arxiv-speech", "hf-asr", "hf-tts"])
        self.assertNotIn("sources", config)
        self.assertEqual(data["site"]["mode"], "automatic_discovery")
        self.assertEqual(data["projects"][0]["id"], "arxiv-speech")
        self.assertTrue(all(event.get("provenance") == "harvested" for event in data["events"]))
        self.assertTrue(all(profile.get("asset_key") for profile in data["profiles"]))
        self.assertTrue((ROOT / "data/legacy-setup.json").exists())

    def test_publish_derives_events_profiles_and_state(self):
        root = self.fixture_root()
        result = harvest.publish_batch(self.batch(), root=root)
        self.assertEqual(result["run"]["status"], "updated")
        data = json.loads((root / "data/data.json").read_text())
        self.assertEqual({event["type"] for event in data["events"]}, {"paper", "model"})
        self.assertEqual({project["id"] for project in data["projects"]}, {"arxiv-speech", "hf-asr", "hf-tts"})
        self.assertEqual(len(data["profiles"]), 2)
        state = json.loads((root / "task/state.json").read_text())
        self.assertEqual(state["discovery"]["arxiv-speech"]["query"], json.loads((root / "task/sources.json").read_text())["discovery"]["feeds"][0]["query"])
        self.assertIsNotNone(state["discovery"]["arxiv-speech"]["cursor"])

    def test_partial_run_keeps_failed_watermark_pending(self):
        root = self.fixture_root()
        result = harvest.publish_batch(self.batch(partial=True), root=root)
        self.assertEqual(result["run"]["status"], "partial")
        state = json.loads((root / "task/discovery_state.json").read_text())
        self.assertIsNone(state["feeds"]["hf-asr"]["cursor"])
        self.assertEqual(state["feeds"]["hf-asr"]["pending_since"], "2026-10-23T12:00:00Z")

    def test_replay_is_immutable_and_deduplicated(self):
        root = self.fixture_root()
        first = harvest.publish_batch(self.batch(), root=root)
        before = json.loads((root / "data/data.json").read_text())
        second = harvest.publish_batch(self.batch(), root=root)
        after = json.loads((root / "data/data.json").read_text())
        self.assertFalse(first["replay"])
        self.assertTrue(second["replay"])
        self.assertEqual(before, after)
        self.assertEqual(len(after["discovery_items"]), 2)

    def test_validate_only_does_not_write_fixture(self):
        root = self.fixture_root()
        before = (root / "data/data.json").read_bytes()
        result = harvest.publish_batch(self.batch(), root=root, validate_only=True)
        self.assertEqual(result["run"]["status"], "updated")
        self.assertEqual(before, (root / "data/data.json").read_bytes())

    def test_provider_url_and_revision_are_strict(self):
        root = self.fixture_root()
        invalid = self.batch()
        invalid["items"][0]["source_url"] = "https://arxiv.org/html/2601.12345v1"
        with self.assertRaises(harvest.HarvestError):
            harvest.publish_batch(invalid, root=root, validate_only=True)

    def test_archive_and_feed_keep_absolute_links_and_legacy_history(self):
        data = json.loads((ROOT / "data/data.json").read_text())
        archive = build.render_archive(data)
        atom = build.feed(data)
        self.assertIn("digests/setup-import-", archive)
        self.assertIn("briefs/brief-setup-import-", archive)
        self.assertIn("https://ahmed-ezzat20.github.io/speech-ai-research-digest/", atom)


if __name__ == "__main__":
    unittest.main()
