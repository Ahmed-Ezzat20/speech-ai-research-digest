#!/usr/bin/env python3
"""Publish one automatic-discovery batch into the static digest.

The collector is deliberately separate from this module.  A batch contains
only public provider metadata; this publisher validates it, appends immutable
receipts/events, and derives the current homepage fields.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import ipaddress
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "data.json"
CONFIG_PATH = ROOT / "task" / "sources.json"
DISCOVERY_STATE_PATH = ROOT / "task" / "discovery_state.json"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
ARXIV_RE = re.compile(r"^[0-9]{4}\.[0-9]{4,5}$")
REV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
BASIS = {"published", "updated", "repository_created", "repository_modified", "observed"}
CATEGORIES = {"Research", "ASR", "TTS"}


class HarvestError(ValueError):
    """Raised when a collector batch cannot be safely published."""


def load_json(path: Path, default: object | None = None):
    if not path.exists():
        if default is not None:
            return copy.deepcopy(default)
        raise HarvestError(f"missing JSON file: {path}")
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise HarvestError(f"invalid JSON in {path}: {exc}") from exc


def iso_dt(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HarvestError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HarvestError(f"{field} is not an ISO timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise HarvestError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def valid_public_url(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise HarvestError(f"{field} must be a public http(s) URL")
    if any(ord(ch) < 32 or ch.isspace() for ch in value):
        raise HarvestError(f"{field} must not contain whitespace/control characters")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise HarvestError(f"{field} must be an HTTPS URL")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or "." not in host or host in {"localhost", "localhost.localdomain"}:
        raise HarvestError(f"{field} must use a public hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_reserved or address.is_link_local):
        raise HarvestError(f"{field} must not target a private/reserved address")
    return value


def check_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise HarvestError(f"{field} has an invalid stable id")
    return value


def canonical_asset(item: dict) -> tuple[str, str, str]:
    """Return (asset key, revision, canonical URL) and reject URL drift."""
    kind = item.get("kind")
    asset = item.get("asset_key")
    revision = item.get("revision")
    source_url = valid_public_url(item.get("source_url"), "item.source_url")
    if not isinstance(asset, str) or not asset.strip() or not isinstance(revision, str) or not REV_RE.fullmatch(revision):
        raise HarvestError("item.asset_key and item.revision are required")
    asset = asset.strip().strip("/")
    parsed = urlsplit(source_url)
    path = parsed.path.strip("/")
    if kind == "paper":
        canonical = re.sub(r"v[0-9]+$", "", asset)
        if not ARXIV_RE.fullmatch(canonical) or not re.fullmatch(r"v[0-9]+", revision) or revision == "unknown":
            raise HarvestError("paper asset_key/revision must be an arXiv id and version")
        expected = f"https://arxiv.org/abs/{canonical}{revision}"
        if source_url.rstrip("/") != expected or parsed.netloc.lower() != "arxiv.org":
            raise HarvestError("paper source_url must be the canonical arXiv abs URL matching asset_key/revision")
        return canonical, revision, expected
    if kind == "model":
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", asset):
            raise HarvestError("model asset_key must be a Hugging Face namespace/repository")
        expected = f"https://huggingface.co/{asset}"
        if source_url.rstrip("/") != expected or parsed.netloc.lower() != "huggingface.co":
            raise HarvestError("model source_url must be the canonical Hugging Face repository URL")
        if revision == "unknown":
            raise HarvestError("model item requires an immutable Hugging Face revision/SHA")
        metadata_sha = (item.get("metadata") or {}).get("sha")
        if metadata_sha is not None and str(metadata_sha) != revision:
            raise HarvestError("model revision must match metadata.sha")
        return asset, revision, expected
    raise HarvestError("item.kind must be paper or model")


def identity(item: dict) -> tuple[str, str, str]:
    asset, revision, _ = canonical_asset(item)
    return item["kind"], asset, revision


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HarvestError(f"metadata is not JSON-safe: {exc}") from exc


def config_feeds(config: dict) -> dict[str, dict]:
    if config.get("schema_version") != 2 or config.get("mode") != "automatic_discovery":
        raise HarvestError("sources.json must be automatic-discovery schema_version 2")
    discovery = config.get("discovery")
    feeds = discovery.get("feeds") if isinstance(discovery, dict) else None
    if not isinstance(feeds, list) or len(feeds) != 3:
        raise HarvestError("configuration must contain exactly three discovery feeds")
    result = {}
    for feed in feeds:
        if not isinstance(feed, dict) or not isinstance(feed.get("id"), str):
            raise HarvestError("each discovery feed needs an id")
        if feed["id"] in result or feed.get("category") not in CATEGORIES:
            raise HarvestError("discovery feed ids/categories must be unique and known")
        if feed.get("kind") == "arxiv":
            if not isinstance(feed.get("query"), str) or not feed["query"].strip():
                raise HarvestError("arXiv feed requires a query")
        elif feed.get("kind") == "huggingface":
            if not isinstance(feed.get("pipeline_tag"), str) or not feed["pipeline_tag"].strip():
                raise HarvestError("Hugging Face feed requires pipeline_tag")
        else:
            raise HarvestError("unsupported discovery feed kind")
        result[feed["id"]] = feed
    policy = config.get("policy") or (discovery.get("policy") if isinstance(discovery, dict) else None)
    if policy is not None and (policy.get("human_approval") is not False or policy.get("auto_publish") is not True):
        raise HarvestError("automatic publisher policy must disable human approval and enable auto publish")
    return result


def validate_item(item: object, feeds: dict[str, dict], checked_at: datetime) -> dict:
    if not isinstance(item, dict):
        raise HarvestError("each discovered item must be an object")
    required = {"id", "asset_key", "feed_id", "kind", "revision", "timestamp", "timestamp_basis", "title", "summary", "source_url", "category", "metadata"}
    missing = required - set(item)
    if missing:
        raise HarvestError(f"discovered item is missing {sorted(missing)}")
    check_id(item["id"], "item.id")
    feed_id = item["feed_id"]
    if feed_id not in feeds:
        raise HarvestError(f"item {item['id']} has unknown feed_id")
    feed = feeds[feed_id]
    if item["category"] != feed["category"]:
        raise HarvestError(f"item {item['id']} category does not match feed")
    if item["kind"] not in {"paper", "model"}:
        raise HarvestError(f"item {item['id']} has invalid kind")
    expected_kind = "paper" if feed["kind"] == "arxiv" else "model"
    if item["kind"] != expected_kind:
        raise HarvestError(f"item {item['id']} kind does not match its provider feed")
    if item["timestamp_basis"] not in BASIS:
        raise HarvestError(f"item {item['id']} has invalid timestamp_basis")
    item_time = iso_dt(item["timestamp"], f"item {item['id']}.timestamp")
    if item_time > checked_at:
        raise HarvestError(f"item {item['id']} timestamp is after batch checked_at")
    if not isinstance(item["title"], str) or not item["title"].strip() or not isinstance(item["summary"], str):
        raise HarvestError(f"item {item['id']} title/summary must be text")
    if not isinstance(item["metadata"], dict):
        raise HarvestError(f"item {item['id']}.metadata must be an object")
    canonical_json(item["metadata"])
    asset, revision, url = canonical_asset(item)
    normalized = copy.deepcopy(item)
    normalized["asset_key"] = asset
    normalized["revision"] = revision
    normalized["source_url"] = url
    normalized["timestamp"] = stamp(item_time)
    return normalized


def validate_batch(batch: object, config: dict) -> tuple[dict, dict[str, dict], list[dict], datetime]:
    if not isinstance(batch, dict):
        raise HarvestError("batch must be a JSON object")
    if batch.get("schema_version") != 1 or batch.get("mode") != "automatic_discovery":
        raise HarvestError("batch must use collector schema_version 1 automatic_discovery")
    checked_at = iso_dt(batch.get("checked_at"), "batch.checked_at")
    if checked_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HarvestError("batch.checked_at cannot be materially in the future")
    feeds = config_feeds(config)
    raw_feeds = batch.get("feeds")
    if not isinstance(raw_feeds, list) or {f.get("id") for f in raw_feeds if isinstance(f, dict)} != set(feeds):
        raise HarvestError("batch must report every configured feed exactly once")
    feed_results = {}
    for raw in raw_feeds:
        if not isinstance(raw, dict) or raw.get("id") not in feeds or raw["id"] in feed_results:
            raise HarvestError("batch contains an invalid or duplicate feed result")
        status = raw.get("status")
        if status not in {"ok", "failed", "partial"}:
            raise HarvestError(f"feed {raw.get('id')} has invalid status")
        if not isinstance(raw.get("complete"), bool) or not isinstance(raw.get("received_count"), int) or raw["received_count"] < 0:
            raise HarvestError(f"feed {raw['id']} requires complete and received_count")
        since = iso_dt(raw.get("since"), f"feed {raw['id']}.since")
        if since > checked_at:
            raise HarvestError(f"feed {raw['id']}.since is after checked_at")
        cursor = raw.get("cursor")
        if cursor is not None:
            cursor_time = iso_dt(cursor, f"feed {raw['id']}.cursor")
            if cursor_time > checked_at:
                raise HarvestError(f"feed {raw['id']}.cursor is after checked_at")
        if status == "ok" and cursor is None:
            raise HarvestError(f"complete feed {raw['id']} needs a successful watermark")
        if status == "ok" and not raw["complete"]:
            raise HarvestError(f"feed {raw['id']} cannot be ok when incomplete")
        if status != "ok" and raw["complete"]:
            raise HarvestError(f"feed {raw['id']} cannot be complete when {status}")
        if status != "ok" and not isinstance(raw.get("error"), str):
            raise HarvestError(f"feed {raw['id']} failure must include an error")
        feed_results[raw["id"]] = copy.deepcopy(raw)
    raw_items = batch.get("items")
    if not isinstance(raw_items, list):
        raise HarvestError("batch.items must be an array")
    items = []
    by_id: dict[str, dict] = {}
    by_identity: dict[tuple[str, str, str], dict] = {}
    for raw in raw_items:
        item = validate_item(raw, feeds, checked_at)
        if item["id"] in by_id:
            if canonical_json(by_id[item["id"]]) != canonical_json(item):
                raise HarvestError(f"conflicting duplicate item id {item['id']}")
            continue
        key = identity(item)
        if key in by_identity:
            if canonical_json(by_identity[key]) != canonical_json(item):
                raise HarvestError(f"conflicting duplicate immutable item identity {key}")
            continue
        by_id[item["id"]] = item
        by_identity[key] = item
        items.append(item)
    counts = {fid: 0 for fid in feeds}
    for item in items:
        counts[item["feed_id"]] += 1
    for fid, result in feed_results.items():
        if counts[fid] > result["received_count"]:
            raise HarvestError(f"feed {fid} received_count is below its item count")
    return copy.deepcopy(batch), feed_results, items, checked_at


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def short_hash(*parts: object, length: int = 16) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def base_data(config: dict, generated_at: str) -> dict:
    feeds = config_feeds(config)
    projects = [{"id": f["id"], "name": f["name"], "color": f["color"], "category": f["category"], "sources": [], "feed_id": f["id"]} for f in feeds.values()]
    return {
        "schema_version": 2,
        "site": {
            "title": "Speech AI Research Digest",
            "description": "Automatically discovered public metadata for speech research; verify every primary source.",
            "url": "https://ahmed-ezzat20.github.io/speech-ai-research-digest",
            "repo_url": "https://github.com/Ahmed-Ezzat20/speech-ai-research-digest",
            "timezone": config.get("timezone", "Africa/Cairo"),
            "mode": "automatic_discovery",
            "tracking_since": generated_at[:10],
            "schedule": copy.deepcopy(config.get("schedule", {})),
            "generated_at": generated_at,
        },
        "projects": projects,
        "profiles": [],
        "events": [],
        "actions": [],
        "runs": [],
        "brief": {},
        "headlines": [],
        "snapshots": [],
        "experiments": [],
        "discovery_items": [],
        "legacy_archive": {"setup_file": "data/legacy-setup.json", "historical_files_preserved": True},
    }


def item_event(item: dict) -> dict:
    title = item["title"].strip()
    return {
        "id": item["id"],
        "project_id": item["feed_id"],
        "timestamp": item["timestamp"],
        "timestamp_basis": item["timestamp_basis"],
        "title": title,
        "type": item["kind"],
        "category": item["category"],
        "summary": item["summary"],
        "source_url": item["source_url"],
        "significance": "routine",
        "provenance": "harvested",
        "evidence_id": item["id"],
        "asset_key": item["asset_key"],
        "revision": item["revision"],
        "metadata": copy.deepcopy(item["metadata"]),
        "action": None,
    }


def latest_profiles(items: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    for item in items:
        key = f"{item['kind']}:{item['asset_key']}"
        if key not in latest or (iso_dt(item["timestamp"], "item.timestamp"), item["revision"]) > (iso_dt(latest[key]["timestamp"], "item.timestamp"), latest[key]["revision"]):
            latest[key] = item
    profiles = []
    for item in sorted(latest.values(), key=lambda x: (x["category"], x["title"].lower(), x["asset_key"])):
        metadata = item.get("metadata", {})
        license_value = metadata.get("license") or metadata.get("license_name") or "Unknown from public metadata."
        language_value = metadata.get("language") or metadata.get("languages") or "Unknown from public metadata."
        profiles.append({
            "id": "profile-" + short_hash(item["kind"], item["asset_key"]),
            "project_id": item["feed_id"],
            "feed_id": item["feed_id"],
            "name": item["title"],
            "category": item["category"],
            "headline": f"Public metadata record · revision {item['revision']}",
            "summary": item["summary"],
            "architecture": str(metadata.get("architecture") or "Unknown from public metadata."),
            "languages": str(language_value),
            "availability": "Public metadata for a provider record; no weights, audio, or remote files were downloaded.",
            "license": str(license_value),
            "sourceDate": item["timestamp"],
            "facts": ["Source metadata excerpt: " + item["summary"]],
            "buildTakeaway": "Hypothesis: evaluate this source-described artifact on a consented, speaker-disjoint Arabic test set; this is not a benchmark result.",
            "caveat": "Repository or paper metadata is not independent evidence of model quality, training-data rights, or Arabic/Egyptian performance.",
            "links": [{"label": "Primary source", "url": item["source_url"]}],
            "asset_key": item["asset_key"],
            "revision": item["revision"],
        })
    return profiles


def derived_experiments(items: list[dict]) -> list[dict]:
    experiments = []
    for item in items:
        experiments.append({
            "id": "hypothesis-" + short_hash(item["id"]),
            "category": item["category"],
            "title": f"Evaluate: {item['title']}",
            "subtitle": "Metadata-derived hypothesis; not a reported result",
            "hypothesis": "Hypothesis: the source-described artifact may be useful for a controlled Arabic speech evaluation, but this digest has not tested it.",
            "steps": [
                "Read the linked primary metadata and record its declared license and language fields.",
                "Prepare a consented, speaker-disjoint Egyptian or Arabic evaluation slice.",
                "Measure the relevant task metrics and report the protocol rather than importing source claims.",
            ],
            "metrics": "Use task-appropriate error, quality, latency, and provenance metrics; no benchmark value is supplied by this digest.",
            "deliverable": "A reproducible evaluation note linked to the exact source revision.",
            "tags": [item["asset_key"], item["revision"], "hypothesis"],
            "source_url": item["source_url"],
        })
    return experiments


def derived_actions(items: list[dict], checked_at: str) -> list[dict]:
    actions = []
    for item in items:
        metadata = item.get("metadata", {})
        license_value = metadata.get("license") or metadata.get("license_name")
        if license_value not in (None, "", "unknown", "Unknown"):
            continue
        actions.append({
            "id": "access-check-" + short_hash(item["id"]),
            "project_id": item["feed_id"],
            "category": "license",
            "title": f"Check access and license terms for {item['title']}",
            "affects": "Public metadata does not establish permission to reuse weights, source audio, or training data.",
            "do": "Read the exact primary card or paper terms before downloading, training, redistributing, or commercializing anything.",
            "source_url": item["source_url"],
            "timestamp": checked_at,
            "dismissed": False,
            "deadline": None,
            "evergreen": True,
            "evidence_id": item["id"],
        })
    return actions


def cairo_window(checked_at: datetime) -> tuple[datetime, datetime]:
    local = checked_at.astimezone(ZoneInfo("Africa/Cairo"))
    start_day = local.date() - timedelta(days=6)
    end_day = local.date() + timedelta(days=1)
    start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=ZoneInfo("Africa/Cairo")).astimezone(timezone.utc)
    end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=ZoneInfo("Africa/Cairo")).astimezone(timezone.utc)
    return start, end


def make_brief(data: dict, checked_at: datetime, batch_items: list[dict], status: str, feed_results: dict[str, dict]) -> dict:
    start, end = cairo_window(checked_at)
    events = [e for e in data.get("events", []) if start <= iso_dt(e["timestamp"], "event.timestamp") < end]
    counts = {project["id"]: 0 for project in data.get("projects", [])}
    for event in events:
        counts[event["project_id"]] = counts.get(event["project_id"], 0) + 1
    event_ids = sorted(e["id"] for e in events)
    by_category: dict[str, list[dict]] = {}
    for item in events:
        by_category.setdefault(item["category"], []).append(item)
    themes = []
    for category, category_items in sorted(by_category.items()):
        urls = [item["source_url"] for item in category_items]
        themes.append({
            "title": f"{category} metadata observed",
            "body": f"{len(category_items)} collected source metadata record(s) fall in the seven-day date window. The linked titles and source summaries are preserved as supplied; no independent quality or release claim is made.",
            "so_what": "Use the exact primary sources to decide what merits a controlled, consented evaluation.",
            "source_urls": urls,
        })
    failures = [fid for fid, result in feed_results.items() if result["status"] != "ok" or not result["complete"]]
    if status == "partial":
        summary = f"Coverage is partial: {len(failures)} of {len(feed_results)} configured feed(s) failed or were truncated. Published records are limited to returned public metadata; no complete no-news claim is made."
        headline = "Partial automatic discovery coverage."
    elif batch_items:
        summary = f"Automatic discovery added {len(batch_items)} new immutable public metadata record(s) across {len(feed_results)} feeds. This is metadata collection, not independently reproduced model quality."
        headline = f"{len(batch_items)} public metadata record(s) discovered."
    else:
        summary = "All configured discovery feeds completed successfully; no new immutable public metadata record was returned."
        headline = "No new public metadata records discovered."
    watch_next = [{"text": f"Retry incomplete feed: {fid}; its watermark remains pending."} for fid in failures]
    return {
        "id": "brief-" + checked_at.strftime("%Y%m%dT%H%M%SZ"),
        "generated_at": stamp(checked_at),
        "period_start": stamp(start),
        "period_end": stamp(end),
        "headline": headline,
        "summary": summary,
        "themes": themes,
        "watch_next": watch_next,
        "counts": counts,
        "event_ids": event_ids,
    }


def update_discovery_state(config: dict, old_state: dict, feed_results: dict[str, dict], checked_at: str) -> dict:
    state = copy.deepcopy(old_state) if isinstance(old_state, dict) else {}
    state.setdefault("schema_version", 1)
    state["mode"] = "automatic_discovery"
    state["timezone"] = config.get("timezone", "Africa/Cairo")
    state["last_checked_at"] = checked_at
    feed_state = state.setdefault("feeds", {})
    for fid, result in feed_results.items():
        prior = feed_state.setdefault(fid, {})
        prior["last_checked_at"] = checked_at
        prior["last_status"] = result["status"]
        prior["last_received_count"] = result["received_count"]
        if result["status"] == "ok" and result["complete"]:
            prior["cursor"] = result.get("cursor")
            prior["pending_since"] = None
        else:
            prior.setdefault("cursor", None)
            prior["pending_since"] = prior.get("pending_since") or result.get("since")
    return state


def build_state_view(config: dict, discovery_state: dict, data: dict) -> dict:
    latest = max(data.get("runs", []), key=lambda run: iso_dt(run["checked_at"], "run.checked_at"), default=None)
    feeds = {}
    for feed in config["discovery"]["feeds"]:
        saved = discovery_state.get("feeds", {}).get(feed["id"], {})
        feeds[feed["id"]] = {key: feed[key] for key in ("kind", "category") if key in feed}
        if "query" in feed:
            feeds[feed["id"]]["query"] = feed["query"]
        if "pipeline_tag" in feed:
            feeds[feed["id"]]["pipeline_tag"] = feed["pipeline_tag"]
        feeds[feed["id"]].update({key: saved.get(key) for key in ("cursor", "pending_since", "last_checked_at", "last_status")})
    return {
        "schema_version": 2,
        "mode": "automatic_discovery",
        "timezone": config.get("timezone", "Africa/Cairo"),
        "schedule": copy.deepcopy(config.get("schedule", {})),
        "last_run": latest["checked_at"] if latest else None,
        "last_entry_file": latest["url"] if latest else None,
        "last_snapshot": max((item.get("generated_at") for item in data.get("snapshots", [])), default=None),
        "discovery": feeds,
    }


def publish_batch(batch: dict | None = None, root: Path = ROOT, validate_only: bool = False) -> dict:
    """Validate and append one batch.  With no batch, invoke discover.collect once."""
    root = Path(root).resolve()
    config_path = root / "task" / "sources.json"
    data_path = root / "data" / "data.json"
    state_path = root / "task" / "discovery_state.json"
    config = load_json(config_path)
    if batch is None:
        sys.path.insert(0, str(root / "scripts"))
        try:
            import discover  # type: ignore
        except ImportError as exc:
            raise HarvestError("automatic collection requires scripts/discover.py") from exc
        state = load_json(state_path, {"schema_version": 1, "feeds": {}})
        batch = discover.collect(config, state)
    batch, feed_results, items, checked_at = validate_batch(batch, config)
    data = load_json(data_path, base_data(config, stamp(checked_at)))
    if not isinstance(data, dict):
        raise HarvestError("data.json must contain an object")
    if data.get("schema_version") != 2:
        raise HarvestError("active data.json must use schema_version 2; preserve old data in legacy-setup.json")
    existing_receipts = {item["id"]: item for item in data.get("discovery_items", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    existing_identity = {}
    for old in data.get("discovery_items", []):
        if isinstance(old, dict) and all(key in old for key in ("kind", "asset_key", "revision")):
            existing_identity[(old["kind"], old["asset_key"], old["revision"])] = old
    new_items = []
    for item in items:
        old = existing_receipts.get(item["id"]) or existing_identity.get(identity(item))
        if old is None:
            new_items.append(item)
        elif identity(old) != identity(item):
            raise HarvestError(f"immutable identity conflict for {item['id']}")
        # API metadata may refresh without a new paper version/model SHA.
        # Original receipts/events stay immutable; current profiles refresh below.
    existing_events = {event["id"]: event for event in data.get("events", []) if isinstance(event, dict) and isinstance(event.get("id"), str)}
    new_events = [item_event(item) for item in new_items if item["id"] not in existing_events]
    old_runs = {record.get("id"): record for record in data.get("runs", []) if isinstance(record, dict)}
    run_id = "harvest-" + checked_at.strftime("%Y%m%dT%H%M%SZ")
    batch_digest = hashlib.sha256(canonical_json({"feeds": batch["feeds"], "items": items}).encode()).hexdigest()
    if run_id in old_runs:
        if new_items or (old_runs[run_id].get("batch_digest") and old_runs[run_id]["batch_digest"] != batch_digest):
            raise HarvestError("a run replay cannot add or change batch records")
        old_state = load_json(state_path, {"schema_version": 1, "feeds": {}})
        state_view = build_state_view(config, old_state, data)
        return {"data": data, "state": old_state, "state_view": state_view, "batch": batch, "run": old_runs[run_id], "new_items": [], "replay": True}
    failed = [fid for fid, result in feed_results.items() if result["status"] != "ok" or not result["complete"]]
    status = "partial" if failed else ("updated" if new_items else "quiet")
    run = {
        "id": run_id,
        "checked_at": stamp(checked_at),
        "status": status,
        "headline": ("Partial automatic discovery coverage." if status == "partial" else (f"{len(new_items)} public metadata record(s) discovered." if new_items else "No new public metadata records discovered.")),
        "event_ids": [event["id"] for event in new_events],
        "sources_checked": sorted(feed_results),
        "failures": [{"source_id": fid, "message": feed_results[fid].get("error") or "feed incomplete or truncated"} for fid in failed],
        "url": f"digests/{run_id}.html",
        "provenance": "automatic_discovery",
        "batch_digest": batch_digest,
    }
    candidate = copy.deepcopy(data)
    if run_id not in old_runs:
        candidate.setdefault("runs", []).append(run)
        candidate.setdefault("headlines", []).append({"run_id": run_id, "checked_at": run["checked_at"], "headline": run["headline"]})
    candidate.setdefault("discovery_items", [])
    candidate["discovery_items"].extend(copy.deepcopy(item) for item in new_items)
    candidate.setdefault("events", [])
    candidate["events"].extend(copy.deepcopy(event) for event in new_events)
    observations = {f"{item['kind']}:{item['asset_key']}": item for item in candidate.get("profile_items", candidate["discovery_items"])}
    for item in items:
        observations[f"{item['kind']}:{item['asset_key']}"] = copy.deepcopy(item)
    candidate["profile_items"] = list(observations.values())
    candidate["profiles"] = latest_profiles(candidate["profile_items"])
    candidate["experiments"] = derived_experiments(new_items[:3])
    candidate.setdefault("actions", [])
    known_actions = {action.get("id") for action in candidate["actions"] if isinstance(action, dict)}
    for action in derived_actions(new_items, stamp(checked_at)):
        if action["id"] not in known_actions:
            candidate["actions"].append(action)
    candidate["brief"] = make_brief(candidate, checked_at, new_items, status, feed_results)
    candidate["site"]["generated_at"] = stamp(checked_at)
    candidate["site"]["mode"] = "automatic_discovery"
    candidate["projects"] = [{"id": feed["id"], "name": feed["name"], "color": feed["color"], "category": feed["category"], "sources": [], "feed_id": feed["id"]} for feed in config["discovery"]["feeds"]]
    candidate["legacy_archive"] = candidate.get("legacy_archive") or {"setup_file": "data/legacy-setup.json", "historical_files_preserved": True}
    snapshots = candidate.setdefault("snapshots", [])
    last_snapshot = max((iso_dt(s["generated_at"], "snapshot.generated_at") for s in snapshots if isinstance(s, dict) and s.get("generated_at")), default=None)
    if last_snapshot is None or checked_at - last_snapshot >= timedelta(days=7):
        snapshot = copy.deepcopy(candidate["brief"])
        snapshot["url"] = f"briefs/{snapshot['id']}.html"
        snapshots.append(snapshot)
    old_state = load_json(state_path, {"schema_version": 1, "feeds": {}})
    next_state = update_discovery_state(config, old_state, feed_results, stamp(checked_at))
    state_view = build_state_view(config, next_state, candidate)
    if validate_only:
        return {"data": candidate, "state": next_state, "state_view": state_view, "batch": batch, "run": run, "new_items": new_items, "replay": run_id in old_runs}
    atomic_json(data_path, candidate)
    atomic_json(state_path, next_state)
    atomic_json(root / "task" / "state.json", state_view)
    try:
        import build
        build.ROOT = root
        build.DATA_PATH = data_path
        build.build()
    except Exception:
        raise
    return {"data": candidate, "state": next_state, "state_view": state_view, "batch": batch, "run": run, "new_items": new_items, "replay": run_id in old_runs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", help="collector batch JSON; absent invokes discover.collect once")
    parser.add_argument("--validate-only", action="store_true", help="validate and derive output without writing")
    parser.add_argument("--root", default=str(ROOT), help="publish repository root")
    args = parser.parse_args()
    try:
        batch = load_json(Path(args.batch)) if args.batch else None
        result = publish_batch(batch=batch, root=Path(args.root), validate_only=args.validate_only)
    except (OSError, HarvestError, KeyError, TypeError) as exc:
        print(f"harvest rejected: {exc}", file=sys.stderr)
        return 2
    print(("validated" if args.validate_only else "published") + f" {result['run']['status']} run {result['run']['id']} ({len(result['new_items'])} new item(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
