import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build
import update


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.data = update.load(update.DATA_PATH)
        self.sources = update.load(update.SOURCES_PATH)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
        ids = [source['id'] for source in self.sources['sources']]
        self.payload = {'run': {'id':'safety-check','checked_at':now,'status':'quiet','headline':'Test only','event_ids':[], 'sources_checked':ids,'failures':[], 'url':'digests/safety-check.html'}, 'events':[],'actions':[],'brief':{}, 'source_state':{source['id']:{'status':'checked','cursor':source.get('cursor')} for source in self.sources['sources']}}

    def event(self):
        event = copy.deepcopy(self.data['events'][0])
        event['id'] = 'test-new-reviewed-event'
        event['timestamp'] = self.payload['run']['checked_at']
        event['provenance'] = 'reviewed'
        return event

    def test_complete_quiet_validation_does_not_mutate_files(self):
        before = update.DATA_PATH.read_bytes()
        result = update.ingest(self.payload, validate_only=True)
        self.assertEqual(result['data']['site']['generated_at'], self.payload['run']['checked_at'])
        self.assertEqual(before, update.DATA_PATH.read_bytes())

    def test_quiet_requires_state_for_every_checked_source(self):
        self.payload['source_state'].pop('whisper')
        with self.assertRaises(update.UpdateError): update.ingest(self.payload, validate_only=True)

    def test_quiet_cannot_hide_unlinked_new_events(self):
        self.payload['events'] = [self.event()]
        with self.assertRaises(update.UpdateError): update.ingest(self.payload, validate_only=True)

    def test_updated_must_cover_all_sources(self):
        event = self.event()
        self.payload['run'].update(status='updated',sources_checked=[event['project_id']],event_ids=[event['id']])
        self.payload['events'] = [event]
        with self.assertRaises(update.UpdateError): update.ingest(self.payload, validate_only=True)

    def test_exact_replay_preserves_the_whole_dataset(self):
        self.payload['run'] = copy.deepcopy(self.data['runs'][0])
        self.payload['source_state'] = {}
        self.payload['brief'] = {'headline':'This must not replace the current brief'}
        result = update.ingest(self.payload, validate_only=True)
        self.assertTrue(result['replay'])
        self.assertEqual(result['data'], self.data)

    def test_wrong_same_host_project_citation_is_rejected(self):
        self.assertFalse(update.registered_source('https://github.com/openai/not-whisper','whisper',self.data))
        self.assertFalse(update.registered_source('https://github.com/openai/whisper/../not-whisper','whisper',self.data))
        self.assertTrue(update.registered_source('https://github.com/openai/whisper/releases/tag/v20250625','whisper',self.data))

    def test_nested_action_schema_is_required(self):
        event = self.event(); event['action'] = {'unexpected':1}
        with self.assertRaises(update.UpdateError): update.validate_event(event,self.data,update.iso_dt(self.payload['run']['checked_at'],'test'))

    def test_setup_cannot_advance_source_cursors(self):
        self.payload['run']['status'] = 'setup'
        with self.assertRaises(update.UpdateError): update.ingest(self.payload, validate_only=True)

    def test_json_script_data_escapes_all_html_openings(self):
        payload = {'sample':'</SCRIPT><script>bad</script><!--'}
        encoded = build.safe_json(payload)
        self.assertNotIn('<', encoded)
        self.assertEqual(json.loads(encoded), payload)


if __name__ == '__main__':
    unittest.main()
