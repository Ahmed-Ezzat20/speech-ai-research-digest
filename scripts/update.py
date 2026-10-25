#!/usr/bin/env python3
"""Validate and ingest a reviewed source-check payload.

Usage: python3 scripts/update.py reviewed.json [--validate-only]
The payload is public JSON only; source collection and editorial review happen
outside this script.  A valid quiet run means every listed source succeeded.
"""
from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, unquote
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / 'data' / 'data.json'
SOURCES_PATH = ROOT / 'task' / 'sources.json'
ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$')
EVENT_TYPES = {'paper','code','weights','dataset','competitor'}
CATEGORIES = {'TTS','ASR','Datasets','Competitors'}
STATUSES = {'updated','quiet','partial','setup'}
FAILURE_WORDS = {'failed','failure','error','partial','not_checked','not checked'}

class UpdateError(ValueError):
    pass


def load(path: Path):
    with path.open(encoding='utf-8') as fh:
        return json.load(fh)


def iso_dt(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(f'{field} must be a non-empty ISO timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:
        raise UpdateError(f'{field} is not an ISO timestamp: {value}') from exc
    if parsed.tzinfo is None:
        raise UpdateError(f'{field} must include a timezone offset')
    return parsed.astimezone(timezone.utc)


def valid_url(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise UpdateError(f'{field} must be a public http(s) URL')
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise UpdateError(f'{field} must not contain whitespace/control characters: {value}')
    parsed = urlsplit(value)
    if parsed.scheme not in {'http','https'} or not parsed.netloc or parsed.username or parsed.password:
        raise UpdateError(f'{field} must be a public http(s) URL: {value}')
    host = (parsed.hostname or '').lower().rstrip('.')
    if not host or host in {'localhost','localhost.localdomain'} or '.' not in host:
        raise UpdateError(f'{field} must use a public hostname: {value}')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise UpdateError(f'{field} must not target a private/reserved address: {value}')
    return value


def check_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise UpdateError(f'{field} has an invalid stable id')
    return value


def same(a: object, b: object) -> bool:
    return json.dumps(a, ensure_ascii=False, sort_keys=True, separators=(',',':')) == json.dumps(b, ensure_ascii=False, sort_keys=True, separators=(',',':'))


def host(value: str) -> str:
    return (urlsplit(value).hostname or '').lower().rstrip('.')


def source_hosts(data: dict) -> dict[str, set[str]]:
    return {p['id']:{host(s['url']) for s in p.get('sources',[]) if isinstance(s,dict) and isinstance(s.get('url'),str)} for p in data.get('projects',[])}


def registered_source(value: str, project_id: str, data: dict) -> bool:
    candidate = urlsplit(valid_url(value, 'source evidence'))
    path = unquote(candidate.path).rstrip('/')
    if any(part in {'.','..'} for part in path.split('/')): return False
    bases = [s['url'] for p in data.get('projects',[]) if p['id'] == project_id for s in p.get('sources',[])]
    bases += [url for s in load(SOURCES_PATH).get('sources',[]) if s.get('project_id') == project_id for url in s.get('urls',[])]
    for base in bases:
        parsed = urlsplit(base)
        root_path = parsed.path.rstrip('/')
        if candidate.hostname != parsed.hostname: continue
        if path == root_path: return True
        if candidate.hostname in {'github.com','huggingface.co'} and path.startswith(root_path + '/'): return True
        if candidate.hostname == 'arxiv.org':
            paper = re.sub(r'v\d+$','',path.rsplit('/',1)[-1])
            registered = re.sub(r'v\d+$','',root_path.rsplit('/',1)[-1])
            if paper == registered and path.startswith(('/abs/','/html/','/pdf/')): return True
    return False


def validate_event(event: dict, data: dict, checked_at: datetime, existing_ids: set[str] | None = None) -> None:
    required = {'id','project_id','timestamp','title','type','category','summary','source_url','significance','provenance','action'}
    missing = required - set(event)
    if missing: raise UpdateError(f'event {event.get("id", "<unknown>")} missing fields: {sorted(missing)}')
    check_id(event['id'], 'event.id')
    projects = {p['id']:p for p in data.get('projects',[])}
    if event['project_id'] not in projects: raise UpdateError(f'event {event["id"]} has unknown project_id')
    if event['type'] not in EVENT_TYPES or event['category'] not in CATEGORIES: raise UpdateError(f'event {event["id"]} has invalid type/category')
    if event['category'] != projects[event['project_id']].get('category'): raise UpdateError(f'event {event["id"]} category does not match project')
    timestamp = iso_dt(event['timestamp'], f'event {event["id"]}.timestamp')
    if timestamp > checked_at: raise UpdateError(f'event {event["id"]} is after its run checked_at')
    url = valid_url(event['source_url'], f'event {event["id"]}.source_url')
    if not registered_source(url,event['project_id'],data): raise UpdateError(f'event {event["id"]}.source_url is not registered for its project')
    if event['significance'] not in {'routine','notable','action'}: raise UpdateError(f'event {event["id"]} has invalid significance')
    if event['provenance'] not in {'reviewed','curated'}: raise UpdateError(f'event {event["id"]} has invalid provenance')
    if not isinstance(event.get('title'),str) or not isinstance(event.get('summary'),str): raise UpdateError(f'event {event["id"]} title/summary must be text')
    if event.get('action') is not None and not isinstance(event['action'],dict): raise UpdateError(f'event {event["id"]}.action must be object or null')
    if event.get('action') is not None:
        action = event['action']
        if not {'category','affects','do','source_url'} <= set(action): raise UpdateError('nested action requires category/affects/do/source_url')
        if action['category'] not in {'license','data','training','evaluation','breaking'}: raise UpdateError('nested action category is invalid')
        if any(not isinstance(action[key],str) or not action[key].strip() for key in ('affects','do')): raise UpdateError('nested action impact/do must be non-empty text')
        if not registered_source(action['source_url'],event['project_id'],data): raise UpdateError('nested action evidence must be registered for its project')


def validate_action(action: dict, data: dict, checked_at: datetime) -> None:
    required = {'id','project_id','category','title','affects','do','source_url','timestamp','dismissed'}
    missing = required - set(action)
    if missing: raise UpdateError(f'action {action.get("id", "<unknown>")} missing fields: {sorted(missing)}')
    check_id(action['id'], 'action.id')
    projects = {p['id']:p for p in data.get('projects',[])}
    if action['project_id'] not in projects: raise UpdateError(f'action {action["id"]} has unknown project_id')
    if action['category'] not in {'license','data','training','evaluation','breaking'}: raise UpdateError(f'action {action["id"]} has invalid category')
    valid_url(action['source_url'], f'action {action["id"]}.source_url')
    if not registered_source(action['source_url'],action['project_id'],data): raise UpdateError(f'action {action["id"]}.source_url is not registered for its project')
    stamp = iso_dt(action['timestamp'], f'action {action["id"]}.timestamp')
    if stamp > checked_at: raise UpdateError(f'action {action["id"]} is after its run checked_at')
    if not isinstance(action['dismissed'],bool): raise UpdateError(f'action {action["id"]}.dismissed must be boolean')
    if action.get('deadline') is not None:
        try: datetime.fromisoformat(str(action['deadline']))
        except ValueError as exc: raise UpdateError(f'action {action["id"]}.deadline must be ISO date or null') from exc
    if 'evergreen' in action and not isinstance(action['evergreen'],bool): raise UpdateError(f'action {action["id"]}.evergreen must be boolean')


def failure_sources(run: dict, source_state: dict) -> set[str]:
    found = set()
    for item in run.get('failures',[]) or []:
        if isinstance(item,dict):
            for key in ('source_id','id','project_id'):
                if item.get(key): found.add(item[key]); break
        elif isinstance(item,str):
            for source_id in source_state:
                if source_id in item: found.add(source_id)
    for source_id, state in source_state.items():
        if isinstance(state,dict) and str(state.get('status','')).lower() in FAILURE_WORDS:
            found.add(source_id)
    return found


def window(checked_at: datetime) -> tuple[datetime,datetime]:
    local = checked_at.astimezone(ZoneInfo('Africa/Cairo'))
    start_day = local.date() - timedelta(days=6)
    end_day = local.date() + timedelta(days=1)
    start = datetime(start_day.year,start_day.month,start_day.day,tzinfo=ZoneInfo('Africa/Cairo')).astimezone(timezone.utc)
    end = datetime(end_day.year,end_day.month,end_day.day,tzinfo=ZoneInfo('Africa/Cairo')).astimezone(timezone.utc)
    return start,end


def rebuild_brief(data: dict, supplied: dict | None, checked_at: datetime) -> dict:
    start,end = window(checked_at)
    brief = copy.deepcopy(supplied or {})
    brief['id'] = brief.get('id') or 'brief-' + checked_at.strftime('%Y%m%dT%H%M%SZ')
    check_id(brief['id'],'brief.id')
    brief['generated_at'] = checked_at.isoformat().replace('+00:00','Z')
    brief['period_start'] = start.isoformat().replace('+00:00','Z')
    brief['period_end'] = end.isoformat().replace('+00:00','Z')
    counts = {p['id']:0 for p in data.get('projects',[])}
    ids = []
    for event in data.get('events',[]):
        stamp = iso_dt(event['timestamp'],'event.timestamp')
        if start <= stamp < end:
            counts[event['project_id']] = counts.get(event['project_id'],0) + 1
            ids.append(event['id'])
    brief['counts'] = counts
    brief['event_ids'] = sorted(ids)
    brief.setdefault('headline','Source-reviewed speech AI check completed.')
    brief.setdefault('summary','The rolling seven-day brief is derived from the verified event ledger.')
    brief.setdefault('themes',[])
    brief.setdefault('watch_next',[])
    return brief


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name+'.', dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as fh:
            json.dump(value,fh,ensure_ascii=False,indent=2)
            fh.write('\n')
            fh.flush(); os.fsync(fh.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def ingest(payload: dict, validate_only: bool=False) -> dict:
    if not isinstance(payload,dict): raise UpdateError('payload must be a JSON object')
    for key in ('run','events','actions','brief','source_state'):
        if key not in payload: raise UpdateError(f'payload missing required key: {key}')
    data = load(DATA_PATH); config = load(SOURCES_PATH)
    run = copy.deepcopy(payload['run'])
    if not isinstance(run,dict): raise UpdateError('run must be an object')
    for key in ('id','checked_at','status','headline','event_ids','sources_checked','failures','url'):
        if key not in run: raise UpdateError(f'run missing field: {key}')
    check_id(run['id'],'run.id'); checked_at = iso_dt(run['checked_at'],'run.checked_at')
    now = datetime.now(timezone.utc)
    if checked_at > now + timedelta(minutes=5): raise UpdateError('run.checked_at cannot be materially in the future')
    if run['status'] not in STATUSES: raise UpdateError('run.status is invalid')
    if run['url'] != f'digests/{run["id"]}.html': raise UpdateError('run.url must be digests/{run.id}.html')
    if not isinstance(run['event_ids'],list) or not isinstance(run['sources_checked'],list) or not isinstance(run['failures'],list): raise UpdateError('run event_ids/sources_checked/failures must be arrays')
    known_sources = {s['id'] for s in config.get('sources',[])}
    if any(s not in known_sources for s in run['sources_checked']): raise UpdateError('run.sources_checked contains unknown source')
    if run['status'] != 'setup' and not run['sources_checked']: raise UpdateError('a non-setup run must list checked sources')
    source_state = payload['source_state']
    if not isinstance(source_state,dict): raise UpdateError('source_state must be an object keyed by source id')
    if any(k not in known_sources for k in source_state): raise UpdateError('source_state contains unknown source')
    if run['status'] == 'setup' and (run['sources_checked'] or source_state or run['event_ids'] or payload['events']): raise UpdateError('setup/import is not a source check and cannot publish events or advance cursors')
    failed = failure_sources(run,{source_id: source_state.get(source_id,{}) for source_id in known_sources})
    if run['status'] != 'setup':
        missing_sources = known_sources - (set(run['sources_checked']) | failed)
        if missing_sources: raise UpdateError(f'run must account for every configured source; missing {sorted(missing_sources)}')
        missing_state = set(run['sources_checked']) - set(source_state)
        if missing_state: raise UpdateError(f'checked sources require explicit reviewed state; missing {sorted(missing_state)}')
    if run['status'] == 'quiet' and run['failures']: raise UpdateError('quiet run cannot contain failures; use partial')
    if run['status'] == 'quiet' and failed: raise UpdateError('quiet run cannot hide failed sources; use partial')
    if run['status'] == 'quiet' and set(run['sources_checked']) != known_sources: raise UpdateError('quiet run must check every configured source')
    if run['status'] == 'quiet' and run['event_ids']: raise UpdateError('quiet run cannot claim reviewed event ids')
    if run['failures'] and run['status'] != 'partial': raise UpdateError('any source failure requires partial status')
    if run['status'] == 'partial' and not run['failures']: raise UpdateError('partial run must record at least one failure')
    if not isinstance(payload['events'],list) or not isinstance(payload['actions'],list): raise UpdateError('events/actions must be arrays')
    existing_events = {e['id']:e for e in data.get('events',[])}
    existing_actions = {a['id']:a for a in data.get('actions',[])}
    incoming_events = {}
    for event in payload['events']:
        validate_event(event,data,checked_at)
        if event['id'] in incoming_events and not same(incoming_events[event['id']],event): raise UpdateError(f'conflicting duplicate event id {event["id"]}')
        if event['id'] in existing_events and not same(existing_events[event['id']],event): raise UpdateError(f'conflicting existing event id {event["id"]}')
        incoming_events[event['id']] = event
    incoming_actions = {}
    for action in payload['actions']:
        validate_action(action,data,checked_at)
        if action['id'] in incoming_actions and not same(incoming_actions[action['id']],action): raise UpdateError(f'conflicting duplicate action id {action["id"]}')
        if action['id'] in existing_actions and not same(existing_actions[action['id']],action): raise UpdateError(f'conflicting existing action id {action["id"]}')
        incoming_actions[action['id']] = action
    if set(incoming_events) - set(run['event_ids']): raise UpdateError('every incoming event must be linked from its run')
    if run['status'] == 'quiet' and (incoming_events or incoming_actions): raise UpdateError('quiet run cannot publish events or actions')
    if run['status'] != 'setup' and any(event['project_id'] not in run['sources_checked'] for event in incoming_events.values()): raise UpdateError('incoming events require a checked source project')
    old_state = {s['id']:s for s in config.get('sources',[])}
    for source_id,state in source_state.items():
        if not isinstance(state,dict): raise UpdateError(f'source_state.{source_id} must be an object')
        old_cursor = old_state[source_id].get('cursor')
        new_cursor = state.get('cursor',old_cursor)
        if source_id in failed and new_cursor != old_cursor: raise UpdateError(f'failed source {source_id} cannot advance cursor')
        if 'source_url' in state: valid_url(state['source_url'],f'source_state.{source_id}.source_url')
    all_event_ids = set(existing_events) | set(incoming_events)
    if any(eid not in all_event_ids for eid in run['event_ids']): raise UpdateError('run.event_ids references an unknown event')
    if run['status'] in {'quiet','partial'} and run['event_ids'] and run['status'] == 'quiet': raise UpdateError('quiet run cannot reference events')
    existing_runs = {r['id']:r for r in data.get('runs',[])}
    if run['id'] in existing_runs and not same(existing_runs[run['id']],run): raise UpdateError(f'conflicting existing run id {run["id"]}')
    candidate = copy.deepcopy(data)
    if run['id'] in existing_runs:
        if set(incoming_events) - set(existing_events) or set(incoming_actions) - set(existing_actions): raise UpdateError('a replay cannot add new ledger records')
        return {'data':candidate,'sources':copy.deepcopy(config),'replay':True}
    for event in incoming_events.values():
        if event['id'] not in existing_events: candidate['events'].append(event)
    for action in incoming_actions.values():
        if action['id'] not in existing_actions: candidate['actions'].append(action)
    replay = run['id'] in existing_runs
    if not replay: candidate['runs'].append(run)
    candidate['brief'] = rebuild_brief(candidate,payload.get('brief'),checked_at)
    if not replay:
        candidate.setdefault('headlines',[]).append({'run_id':run['id'],'checked_at':run['checked_at'],'headline':run['headline']})
    snapshots = candidate.setdefault('snapshots',[])
    last_snapshot = max((iso_dt(s['generated_at'],'snapshot.generated_at') for s in snapshots), default=None)
    if not replay and (last_snapshot is None or checked_at - last_snapshot >= timedelta(days=7)):
        snap = copy.deepcopy(candidate['brief'])
        snap['url'] = f'briefs/{snap["id"]}.html'
        snapshots.append(snap)
    candidate['site']['generated_at'] = run['checked_at']
    new_config = copy.deepcopy(config)
    if not replay:
        new_config['last_run'] = run['id']
        for source in new_config.get('sources',[]):
            source_id = source['id']
            if source_id in run['sources_checked']:
                state = source_state.get(source_id,{})
                if source_id not in failed and 'cursor' in state: source['cursor'] = state['cursor']
                source['last_run'] = run['id']; source['last_checked_at'] = run['checked_at']; source['last_status'] = 'failed' if source_id in failed else 'checked'
        if len(candidate['snapshots']) > len(data.get('snapshots',[])): new_config['last_snapshot'] = candidate['brief']['generated_at']
    if validate_only: return {'data':candidate,'sources':new_config,'replay':replay}
    atomic_json(DATA_PATH,candidate); atomic_json(SOURCES_PATH,new_config)
    try:
        sys.path.insert(0,str(ROOT/'scripts'))
        import build
        build.build()
    except Exception:
        # Data/config writes remain valid append-only state; report the build error.
        raise
    return {'data':candidate,'sources':new_config,'replay':replay}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload', help='reviewed JSON payload')
    parser.add_argument('--validate-only', action='store_true', help='validate and derive output without writing files')
    args = parser.parse_args()
    try:
        result = ingest(load(Path(args.payload)), args.validate_only)
    except (OSError, json.JSONDecodeError, UpdateError, KeyError) as exc:
        print(f'update rejected: {exc}', file=sys.stderr); return 2
    print(('validated' if args.validate_only else 'ingested') + f' run; replay={result["replay"]}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
