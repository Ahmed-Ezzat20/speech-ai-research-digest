import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402
import harvest  # noqa: E402
import test_pipeline  # noqa: E402


class PublisherSafetyTests(unittest.TestCase):
    def fixture(self):
        helper = test_pipeline.PublisherPipelineTests("test_publish_derives_events_profiles_and_state")
        root = helper.fixture_root()
        self.addCleanup(helper.doCleanups)
        return root, helper

    def test_json_script_data_escapes_all_html_openings(self):
        payload = {"sample": "</SCRIPT><script>bad</script><!--"}
        encoded = build.safe_json(payload)
        self.assertNotIn("<", encoded)
        self.assertEqual(json.loads(encoded), payload)

    def test_conflicting_immutable_identity_is_rejected(self):
        root, helper = self.fixture()
        batch = helper.batch()
        conflicting = copy.deepcopy(batch)
        conflicting["items"][0]["id"] = "paper-other"
        conflicting["items"][0]["summary"] = "different immutable payload"
        batch["items"].append(conflicting["items"][0])
        with self.assertRaises(harvest.HarvestError):
            harvest.publish_batch(batch, root=root, validate_only=True)

    def test_future_item_timestamp_is_rejected(self):
        root, helper = self.fixture()
        batch = helper.batch()
        batch["items"][0]["timestamp"] = "2026-10-25T12:00:01Z"
        with self.assertRaises(harvest.HarvestError):
            harvest.publish_batch(batch, root=root, validate_only=True)

    def test_partial_failure_is_visible_in_baseline_and_run_page(self):
        root, helper = self.fixture()
        result = harvest.publish_batch(helper.batch(partial=True), root=root, validate_only=True)
        rendered = build.render_baseline(result["data"])
        self.assertIn("Partial automatic discovery coverage", rendered)
        self.assertIn("hf-asr", rendered)
        self.assertEqual(result["run"]["status"], "partial")

    def test_active_data_contains_no_seeded_legacy_records(self):
        data = json.loads((ROOT / "data/data.json").read_text())
        self.assertEqual(data["site"]["mode"], "automatic_discovery")
        channels = {"arxiv-speech", "hf-asr", "hf-tts"}
        self.assertEqual({project["id"] for project in data["projects"]}, channels)
        for event in data["events"]:
            self.assertEqual(event["provenance"], "harvested")
            self.assertIn(event["project_id"], channels)
        for profile in data["profiles"]:
            self.assertTrue(profile.get("asset_key"))
            self.assertIn(profile.get("feed_id"), channels)
        self.assertGreater(len(json.loads((ROOT / "data/legacy-setup.json").read_text())["events"]), 0)


if __name__ == "__main__":
    unittest.main()
