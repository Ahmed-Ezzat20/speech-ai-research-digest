import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import discover
import harvest
import build
import test_pipeline

class AutomaticRegressions(unittest.TestCase):
    def setUp(self):
        self.helper = test_pipeline.PublisherPipelineTests('test_publish_derives_events_profiles_and_state')
        self.root = self.helper.fixture_root()
        self.addCleanup(self.helper.doCleanups)

    def test_same_revision_metadata_refresh_does_not_duplicate_events(self):
        first = self.helper.batch()
        original = harvest.publish_batch(first, root=self.root)
        second = copy.deepcopy(first)
        second['checked_at'] = '2026-10-25T12:01:00Z'
        second['items'][1]['metadata']['language'] = ['ar']
        second['items'][1]['summary'] = 'Refreshed public card metadata, same repository SHA.'
        refreshed = harvest.publish_batch(second, root=self.root)
        self.assertEqual(len(refreshed['data']['events']),2)
        self.assertEqual(len(refreshed['data']['discovery_items']),2)
        self.assertEqual(refreshed['run']['status'],'quiet')
        model = next(profile for profile in refreshed['data']['profiles'] if profile['category']=='ASR')
        self.assertIn('ar',model['languages'])
        self.assertEqual(original['data']['discovery_items'][1]['summary'], first['items'][1]['summary'])

    def test_complete_feed_cannot_have_null_or_future_cursor(self):
        for cursor in (None,'2027-01-01T00:00:00Z'):
            batch=self.helper.batch()
            batch['feeds'][0]['cursor']=cursor
            with self.assertRaises(harvest.HarvestError):
                harvest.publish_batch(batch, root=self.root, validate_only=True)

    def test_conflicting_replay_is_rejected(self):
        batch=self.helper.batch()
        harvest.publish_batch(batch, root=self.root)
        batch['items'][0]['summary']='Changed batch under existing run ID'
        with self.assertRaises(harvest.HarvestError):
            harvest.publish_batch(batch, root=self.root, validate_only=True)

    def test_theme_citations_survive_historical_brief_generation(self):
        source='https://arxiv.org/abs/2601.12345v1'
        rendered=build.render_brief_page({'id':'fixture','themes':[{'title':'Speech','body':'Metadata','so_what':'Evaluate','source_urls':[source]}]})
        self.assertIn(f'href="{source}"', rendered)

    def test_failed_feed_with_prior_watermark_remains_json_safe(self):
        from datetime import datetime, timezone
        config=json.loads((ROOT/'task/sources.json').read_text())
        state={'feeds':{'arxiv-speech':{'cursor':'2026-10-20T12:00:00Z'}}}
        with patch.object(discover,'_arxiv_records',side_effect=RuntimeError('fixture offline')), patch.object(discover,'_hf_records',return_value=[]):
            batch=discover.collect(config,state,now=datetime(2026,10,25,12,tzinfo=timezone.utc))
        json.dumps(batch)
        self.assertEqual(batch['feeds'][0]['cursor'],'2026-10-20T12:00:00Z')

if __name__ == '__main__':
    unittest.main()
