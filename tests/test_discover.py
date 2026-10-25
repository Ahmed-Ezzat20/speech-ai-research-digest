import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import discover  # noqa: E402


UTC = timezone.utc


class Author:
    def __init__(self, name):
        self.name = name


class Paper:
    def __init__(self, entry_id, title, updated, published=None, summary="Speech recognition abstract"):
        self.entry_id = entry_id
        self.title = title
        self.summary = summary
        self.updated = updated
        self.published = published or updated - timedelta(days=1)
        self.authors = [Author("Test Author")]
        self.categories = ["cs.CL"]
        self.primary_category = "cs.CL"
        self.comment = ""
        self.doi = ""


class Model:
    def __init__(self, model_id, modified, sha="abc123", private=False, card_data=None):
        self.id = model_id
        self.author = model_id.split("/", 1)[0]
        self.created_at = modified - timedelta(days=3)
        self.last_modified = modified
        self.sha = sha
        self.tags = ["speech"]
        self.pipeline_tag = "automatic-speech-recognition"
        self.siblings = [types.SimpleNamespace(rfilename="config.json")]
        self.card_data = card_data
        self.gated = False
        self.private = private


def config_for(*feeds, **overrides):
    discovery = {
        "lookback_days": 7,
        "overlap_hours": 48,
        "max_results_per_feed": 300,
        "max_retries": 0,
        "retry_delay_seconds": 0,
        "speech_relevance": {"enabled": True},
        "feeds": list(feeds),
    }
    discovery.update(overrides)
    return {"schema_version": 2, "mode": "automatic_discovery", "discovery": discovery}


class DiscoveryTests(unittest.TestCase):
    def test_collects_arxiv_with_stable_versioned_identity_and_skips_music(self):
        checked = datetime(2026, 10, 25, 12, tzinfo=UTC)
        paper = Paper("https://arxiv.org/abs/2601.12345v2", "Speech codec", checked - timedelta(hours=2))
        music = Paper("https://arxiv.org/abs/2601.12346v1", "Music acoustic scene classification", checked - timedelta(hours=1), summary="Music acoustic scene")
        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def results(self, search):
                self.search = search
                return [paper, music]
        fake_arxiv = types.SimpleNamespace(
            Client=FakeClient,
            Search=lambda **kwargs: types.SimpleNamespace(**kwargs),
            SortCriterion=types.SimpleNamespace(LastUpdatedDate="lastUpdatedDate"),
            SortOrder=types.SimpleNamespace(Descending="descending"),
        )
        state = {}
        with patch.dict(sys.modules, {"arxiv": fake_arxiv}):
            batch = discover.collect(config_for({"id": "arxiv-speech", "kind": "arxiv", "category": "Research", "query": "speech"}), state, checked)
        self.assertEqual(batch["feeds"][0]["status"], "ok")
        self.assertEqual(len(batch["items"]), 1)
        item = batch["items"][0]
        self.assertEqual(item["asset_key"], "2601.12345")
        self.assertEqual(item["revision"], "v2")
        self.assertTrue(item["source_url"].startswith("https://arxiv.org/abs/2601.12345v2"))
        self.assertEqual(item["timestamp_basis"], "updated")
        self.assertEqual(state["feeds"]["arxiv-speech"]["cursor"], batch["checked_at"])

    def test_huggingface_uses_no_direction_and_preserves_unknown_metadata(self):
        checked = datetime(2026, 10, 25, 12, tzinfo=UTC)
        model = Model("org/speech-model", checked - timedelta(hours=3), card_data=None)
        calls = []
        class FakeApi:
            def __init__(self, token=False):
                self.token = token
            def list_models(self, **kwargs):
                calls.append(kwargs)
                return [model]
        fake_hf = types.SimpleNamespace(HfApi=FakeApi)
        with patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
            batch = discover.collect(config_for({"id": "hf-asr", "kind": "huggingface", "category": "ASR", "pipeline_tag": "automatic-speech-recognition"}), {}, checked)
        item = batch["items"][0]
        self.assertEqual(batch["feeds"][0]["status"], "ok")
        self.assertEqual(item["revision"], "abc123")
        self.assertEqual(item["metadata"]["license"], None)
        self.assertEqual(item["metadata"]["language"], None)
        self.assertEqual(calls[0]["sort"], "last_modified")
        self.assertNotIn("direction", calls[0])
        self.assertIn("expand", calls[0])
        self.assertNotIn("full", calls[0])

    def test_cap_before_boundary_is_partial_and_does_not_advance_cursor(self):
        checked = datetime(2026, 10, 25, 12, tzinfo=UTC)
        old_cursor = checked - timedelta(days=2)
        paper = Paper("2601.12345v1", "Speech paper", checked - timedelta(hours=1))
        fake_arxiv = types.SimpleNamespace(
            Client=lambda **kwargs: types.SimpleNamespace(results=lambda search: [paper]),
            Search=lambda **kwargs: types.SimpleNamespace(**kwargs),
            SortCriterion=types.SimpleNamespace(LastUpdatedDate="lastUpdatedDate"),
            SortOrder=types.SimpleNamespace(Descending="descending"),
        )
        state = {"feeds": {"arxiv-speech": {"cursor": discover._iso(old_cursor)}}}
        config = config_for({"id": "arxiv-speech", "kind": "arxiv", "category": "Research", "query": "speech"}, max_results_per_feed=1)
        with patch.dict(sys.modules, {"arxiv": fake_arxiv}):
            batch = discover.collect(config, state, checked)
        self.assertEqual(batch["feeds"][0]["status"], "partial")
        self.assertFalse(batch["feeds"][0]["complete"])
        self.assertEqual(batch["feeds"][0]["cursor"], discover._iso(old_cursor))
        self.assertEqual(state["feeds"]["arxiv-speech"]["cursor"], discover._iso(old_cursor))
        self.assertIn("pending_since", state["feeds"]["arxiv-speech"])

    def test_failed_initial_window_is_reused(self):
        checked = datetime(2026, 10, 25, 12, tzinfo=UTC)
        class BrokenApi:
            def __init__(self, token=False):
                pass
            def list_models(self, **kwargs):
                raise RuntimeError("fixture network failure")
        state = {}
        fake_hf = types.SimpleNamespace(HfApi=BrokenApi)
        feed = {"id": "hf-tts", "kind": "huggingface", "category": "TTS", "pipeline_tag": "text-to-speech"}
        with patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
            first = discover.collect(config_for(feed), state, checked)
            second = discover.collect(config_for(feed), state, checked + timedelta(days=1))
        self.assertEqual(first["feeds"][0]["status"], "failed")
        self.assertEqual(second["feeds"][0]["since"], first["feeds"][0]["since"])
        self.assertFalse(second["feeds"][0]["complete"])

    def test_batch_is_json_safe_and_cli_preserves_durable_state(self):
        checked = datetime(2026, 10, 25, 12, tzinfo=UTC)
        fake_arxiv = types.SimpleNamespace(
            Client=lambda **kwargs: types.SimpleNamespace(results=lambda search: []),
            Search=lambda **kwargs: types.SimpleNamespace(**kwargs),
            SortCriterion=types.SimpleNamespace(LastUpdatedDate="lastUpdatedDate"),
            SortOrder=types.SimpleNamespace(Descending="descending"),
        )
        config = config_for({"id": "arxiv-speech", "kind": "arxiv", "category": "Research", "query": "speech"})
        with TemporaryDirectory() as temp:
            temp = Path(temp)
            config_path = temp / "config.json"
            state_path = temp / "state.json"
            output_path = temp / "batch.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            state_path.write_text("{}", encoding="utf-8")
            with patch.dict(sys.modules, {"arxiv": fake_arxiv}), patch.object(discover, "_now", return_value=checked):
                self.assertEqual(discover.main(["--output", str(output_path), "--config", str(config_path), "--state", str(state_path)]), 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "automatic_discovery")
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), {})


if __name__ == "__main__":
    unittest.main()
