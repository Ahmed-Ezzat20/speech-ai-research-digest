#!/usr/bin/env python3
"""Automatic-only speech paper and model metadata discovery.

The provider SDKs are deliberately imported inside provider functions.  This keeps
standard-library publisher/test imports usable outside the discovery virtualenv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

UTC = timezone.utc
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_OVERLAP_HOURS = 48
DEFAULT_MAX_RESULTS = 300
DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 3.0
MAX_TEXT = 200_000
MAX_METADATA_ITEMS = 300

DEFAULT_INCLUDE = (
    "speech", "spoken", "voice", "asr", "automatic speech recognition",
    "tts", "text-to-speech", "speech synthesis", "speech recognition",
    "voice conversion", "audio codec",
)
DEFAULT_EXCLUDE = (
    "music", "acoustic scene", "environmental sound", "bioacoustic",
    "animal sound", "seismic acoustic",
)


def _utc(value: Any, default: datetime | None = None) -> datetime | None:
    if value is None:
        return default
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return default
        try:
            result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return default
    else:
        return default
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _iso(value: datetime) -> str:
    value = _utc(value, datetime.now(UTC)) or datetime.now(UTC)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _now(value: datetime | None) -> datetime:
    result = _utc(value, datetime.now(UTC)) or datetime.now(UTC)
    return result


def _safe_text(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:limit]


def _json_safe(value: Any, depth: int = 0) -> Any:
    """Convert SDK metadata without serializing arbitrary objects or huge cards."""
    if depth > 4:
        return None
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, str):
            return value[:MAX_TEXT]
        return value
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, depth + 1) for item in list(value)[:MAX_METADATA_ITEMS]]
    if isinstance(value, dict):
        return {
            str(key)[:200]: _json_safe(item, depth + 1)
            for key, item in list(value.items())[:MAX_METADATA_ITEMS]
        }
    # ModelCardData and small SDK records expose metadata through to_dict or vars.
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _json_safe(to_dict(), depth + 1)
        except Exception:
            return None
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return _json_safe(attrs, depth + 1)
    return _safe_text(value, 1000)


def _config_section(config: dict[str, Any]) -> dict[str, Any]:
    section = config.get("discovery")
    return section if isinstance(section, dict) else config


def _feed_state_container(state: dict[str, Any]) -> dict[str, Any]:
    for key in ("feeds", "discovery", "sources"):
        value = state.get(key)
        if isinstance(value, dict):
            # A discovery wrapper may itself contain the feed map.
            nested = value.get("feeds")
            if isinstance(nested, dict):
                return nested
            return value
    state["feeds"] = {}
    return state["feeds"]


def _feed_state(state: dict[str, Any], feed_id: str) -> dict[str, Any]:
    feeds = _feed_state_container(state)
    value = feeds.get(feed_id)
    if not isinstance(value, dict):
        value = {}
        feeds[feed_id] = value
    return value


def _window(feed_state: dict[str, Any], checked: datetime, lookback_days: int, overlap_hours: int) -> datetime:
    pending = _utc(feed_state.get("pending_since"))
    if pending is not None:
        return min(pending, checked)
    cursor = _utc(feed_state.get("cursor"))
    if cursor is not None:
        return min(cursor - timedelta(hours=max(0, overlap_hours)), checked)
    return checked - timedelta(days=max(0, lookback_days))


def _provider_error(exc: BaseException) -> str:
    text = _safe_text(exc, 500)
    return text or exc.__class__.__name__


def _retry_call(factory: Callable[[], Any], retries: int, delay: float) -> Any:
    last: BaseException | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            return factory()
        except Exception as exc:  # provider/network failures are part of the batch contract
            last = exc
            if attempt < max(0, retries):
                if delay > 0:
                    time.sleep(delay)
    assert last is not None
    raise last


def _speech_relevant(result: Any, rules: Any) -> bool:
    if not isinstance(rules, dict):
        rules = {}
    if rules.get("enabled", True) is False:
        return True
    title = _safe_text(getattr(result, "title", ""))
    summary = _safe_text(getattr(result, "summary", ""))
    categories = _safe_text(getattr(result, "categories", ""))
    haystack = " ".join((title, summary, categories))
    if not rules.get("case_sensitive", False):
        haystack = haystack.lower()
    include = rules.get("include_any", DEFAULT_INCLUDE)
    exclude = rules.get("exclude_any", DEFAULT_EXCLUDE)
    if isinstance(include, str):
        include = [include]
    if isinstance(exclude, str):
        exclude = [exclude]
    def present(term: Any) -> bool:
        value = _safe_text(term)
        return value in haystack if rules.get("case_sensitive", False) else value.lower() in haystack
    has_include = any(present(term) for term in include or ())
    if not has_include:
        return False
    # A speech hit wins over broad words such as "audio" or "voice" only when
    # the configured include list explicitly contains a strong speech term.
    strong = ("speech", "spoken", "voice", "asr", "tts", "text-to-speech")
    has_strong = any(present(term) for term in strong)
    if any(present(term) for term in exclude or ()) and not has_strong:
        return False
    return True


def _arxiv_parts(entry_id: Any) -> tuple[str, str, str] | None:
    raw = _safe_text(entry_id, 500)
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.netloc:
        if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != "arxiv.org":
            return None
        path = parsed.path
    else:
        path = raw.split("?", 1)[0].split("#", 1)[0]
    path = path.strip().strip("/")
    if path.startswith("abs/"):
        path = path[4:]
    elif path.startswith("pdf/"):
        path = path[4:]
    if path.endswith(".pdf"):
        path = path[:-4]
    if not path or "/" not in path and not re.fullmatch(r"[A-Za-z0-9._-]+", path):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?", path):
        return None
    if any(part in (".", "..") for part in path.split("/")):
        return None
    match = re.match(r"^(.*?)(v[0-9]+)$", path)
    asset_key = match.group(1) if match else path
    revision = match.group(2) if match else "unknown"
    if not asset_key:
        return None
    return asset_key, revision, f"https://arxiv.org/abs/{path}"


def _paper_item(result: Any, feed: dict[str, Any], checked: datetime, rules: Any) -> dict[str, Any] | None:
    if not _speech_relevant(result, rules):
        return None
    parts = _arxiv_parts(getattr(result, "entry_id", ""))
    if parts is None:
        return None
    asset_key, revision, source_url = parts
    updated = _utc(getattr(result, "updated", None))
    published = _utc(getattr(result, "published", None))
    source_time = updated or published
    basis = "updated" if updated else "published"
    if source_time is None:
        source_time = checked
        basis = "observed"
    if source_time > checked:
        source_time = checked
        basis = "observed"
    authors = []
    for author in list(getattr(result, "authors", []) or [])[:MAX_METADATA_ITEMS]:
        name = _safe_text(getattr(author, "name", author), 500)
        if name:
            authors.append(name)
    metadata = {
        "authors": authors,
        "abstract": _safe_text(getattr(result, "summary", "")),
        "published": _iso(published) if published else None,
        "updated": _iso(updated) if updated else None,
        "primary_category": _safe_text(getattr(result, "primary_category", ""), 200) or None,
        "categories": _json_safe(getattr(result, "categories", [])),
        "comment": _safe_text(getattr(result, "comment", "")) or None,
        "doi": _safe_text(getattr(result, "doi", ""), 1000) or None,
    }
    return _item(
        feed, "paper", asset_key, revision, source_time, basis, _safe_text(getattr(result, "title", "")),
        _safe_text(getattr(result, "summary", "")), source_url, metadata,
    )


def _model_card_dict(model: Any) -> dict[str, Any]:
    raw = getattr(model, "card_data", None)
    converted = _json_safe(raw)
    return converted if isinstance(converted, dict) else {}


def _model_item(model: Any, feed: dict[str, Any], checked: datetime) -> dict[str, Any] | None:
    asset_key = _safe_text(getattr(model, "id", ""), 300).strip().strip("/")
    if not re.fullmatch(r"[^/?#\s]+/[^/?#\s]+", asset_key):
        return None
    if getattr(model, "private", False) is True or getattr(model, "gated", False) not in (False, None):
        return None
    source_url = f"https://huggingface.co/{asset_key}"
    sha = _safe_text(getattr(model, "sha", ""), 200) or None
    revision = sha or "unknown"
    modified = _utc(getattr(model, "last_modified", None))
    created = _utc(getattr(model, "created_at", None))
    source_time = modified or created
    basis = "repository_modified" if modified else "repository_created"
    if source_time is None:
        source_time = checked
        basis = "observed"
    if source_time > checked:
        source_time = checked
        basis = "observed"
    card = _model_card_dict(model)
    language = card.get("language")
    license_value = card.get("license") or card.get("license_name")
    files = []
    for sibling in list(getattr(model, "siblings", []) or [])[:MAX_METADATA_ITEMS]:
        name = _safe_text(getattr(sibling, "rfilename", sibling), 500)
        if name:
            files.append(name)
    author = _safe_text(getattr(model, "author", ""), 500)
    tags = _json_safe(getattr(model, "tags", []))
    summary = card.get("description") if isinstance(card.get("description"), str) else ""
    metadata = {
        "authors": [author] if author else [],
        "abstract": _safe_text(summary),
        "created_at": _iso(created) if created else None,
        "updated_at": _iso(modified) if modified else None,
        "tags": tags,
        "pipeline_tag": _safe_text(getattr(model, "pipeline_tag", ""), 200) or None,
        "license": _json_safe(license_value),
        "language": _json_safe(language),
        "sha": sha,
        "files": files,
        "card_data": card,
        "gated": _json_safe(getattr(model, "gated", None)),
    }
    return _item(
        feed, "model", asset_key, revision, source_time, basis,
        asset_key, _safe_text(summary), source_url, metadata,
    )


def _item(feed: dict[str, Any], kind: str, asset_key: str, revision: str, timestamp: datetime,
          basis: str, title: str, summary: str, source_url: str, metadata: dict[str, Any]) -> dict[str, Any]:
    identity = f"{feed.get('id', '')}\0{kind}\0{asset_key}\0{revision}"
    item_id = f"{kind}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
    return {
        "id": item_id,
        "asset_key": asset_key,
        "feed_id": _safe_text(feed.get("id", ""), 100),
        "kind": kind,
        "revision": revision,
        "timestamp": _iso(timestamp),
        "timestamp_basis": basis,
        "title": title or asset_key,
        "summary": summary,
        "source_url": source_url,
        "category": _safe_text(feed.get("category", ""), 100),
        "metadata": _json_safe(metadata),
    }


def _arxiv_records(feed: dict[str, Any], maximum: int, boundary: datetime, retries: int, retry_delay: float) -> list[Any]:
    import arxiv  # lazy: publisher imports do not require the discovery venv
    client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=2)
    criterion = getattr(arxiv.SortCriterion, "LastUpdatedDate", None)
    order = getattr(arxiv.SortOrder, "Descending", None)
    def request() -> list[Any]:
        kwargs: dict[str, Any] = {
            "query": _safe_text(feed.get("query", ""), 2000),
            "max_results": maximum,
        }
        if criterion is not None:
            kwargs["sort_by"] = criterion
        if order is not None:
            kwargs["sort_order"] = order
        records = []
        for record in client.results(arxiv.Search(**kwargs)):
            if len(records) >= maximum:
                break
            source_time = _utc(getattr(record, "updated", None)) or _utc(getattr(record, "published", None))
            if source_time is not None and source_time < boundary:
                break
            records.append(record)
        return records
    return _retry_call(request, retries, retry_delay)


def _hf_records(feed: dict[str, Any], maximum: int, boundary: datetime, retries: int, retry_delay: float) -> list[Any]:
    from huggingface_hub import HfApi  # lazy import by design
    api = HfApi(token=False)
    expansion = ["author", "cardData", "createdAt", "lastModified", "sha", "tags",
                 "pipeline_tag", "siblings", "gated", "private"]
    def request() -> list[Any]:
        records = []
        for model in api.list_models(
            pipeline_tag=_safe_text(feed.get("pipeline_tag", ""), 200),
            sort="last_modified",
            limit=maximum,
            expand=expansion,
            token=False,
        ):
            if len(records) >= maximum:
                break
            source_time = _utc(getattr(model, "last_modified", None)) or _utc(getattr(model, "created_at", None))
            if source_time is not None and source_time < boundary:
                break
            records.append(model)
        return records
    return _retry_call(request, retries, retry_delay)


def _collect_feed(feed: dict[str, Any], feed_state: dict[str, Any], checked: datetime,
                  section: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    feed_id = _safe_text(feed.get("id", ""), 100)
    lookback = int(section.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
    overlap = int(section.get("overlap_hours", DEFAULT_OVERLAP_HOURS))
    maximum = max(1, int(section.get("max_results_per_feed", DEFAULT_MAX_RESULTS)))
    boundary = _window(feed_state, checked, lookback, overlap)
    since = _iso(boundary)
    retries = int(section.get("max_retries", DEFAULT_RETRIES))
    retry_delay = float(section.get("retry_delay_seconds", DEFAULT_RETRY_DELAY))
    kind = _safe_text(feed.get("kind", ""), 40).lower()
    old_cursor = _utc(feed_state.get("cursor"))
    result = {"id": feed_id, "status": "failed", "since": since, "complete": False,
              "cursor": _iso(old_cursor) if old_cursor else None, "received_count": 0, "error": None}
    try:
        if kind == "arxiv":
            records = _arxiv_records(feed, maximum, boundary, retries, retry_delay)
        elif kind == "huggingface":
            records = _hf_records(feed, maximum, boundary, retries, retry_delay)
        else:
            raise ValueError(f"unsupported feed kind: {kind or 'missing'}")
    except Exception as exc:
        result["error"] = _provider_error(exc)
        feed_state["pending_since"] = since
        return result, []
    result["received_count"] = len(records)
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    parse_errors = 0
    unknown_dates = 0
    for record in records[:maximum]:
        try:
            if kind == "arxiv":
                source_time = _utc(getattr(record, "updated", None)) or _utc(getattr(record, "published", None))
                if source_time is not None and source_time < boundary:
                    continue
                if source_time is None:
                    unknown_dates += 1
                item = _paper_item(record, feed, checked, feed.get("speech_relevance", section.get("speech_relevance")))
            else:
                source_time = _utc(getattr(record, "last_modified", None)) or _utc(getattr(record, "created_at", None))
                if source_time is not None and source_time < boundary:
                    continue
                if source_time is None:
                    unknown_dates += 1
                item = _model_item(record, feed, checked)
            if item is None:
                continue
            identity = (item["asset_key"], item["revision"])
            if identity in seen:
                continue
            seen.add(identity)
            items.append(item)
        except Exception:
            parse_errors += 1
    last_time = None
    for record in records[:maximum]:
        candidate = (_utc(getattr(record, "updated", None)) or _utc(getattr(record, "published", None))) if kind == "arxiv" else (_utc(getattr(record, "last_modified", None)) or _utc(getattr(record, "created_at", None)))
        if candidate is not None and (last_time is None or candidate < last_time):
            last_time = candidate
    truncated = len(records) >= maximum and (last_time is None or last_time > boundary)
    if truncated:
        result["status"] = "partial"
        result["error"] = "result cap reached before the date boundary"
    elif parse_errors:
        result["status"] = "partial"
        result["error"] = f"{parse_errors} result(s) could not be normalized"
    elif unknown_dates:
        result["status"] = "partial"
        result["error"] = f"{unknown_dates} result(s) had no provider date"
    else:
        result["status"] = "ok"
        result["complete"] = True
        result["cursor"] = _iso(checked)
        feed_state["cursor"] = result["cursor"]
        feed_state.pop("pending_since", None)
    if result["status"] != "ok":
        feed_state["pending_since"] = feed_state.get("pending_since", since)
    if result["cursor"] is not None and isinstance(result["cursor"], datetime):
        result["cursor"] = _iso(result["cursor"])
    return result, items


def collect(config: dict[str, Any], state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Collect one bounded automatic discovery batch without downloading artifacts."""
    config = config if isinstance(config, dict) else {}
    state = state if isinstance(state, dict) else {}
    checked = _now(now)
    section = _config_section(config)
    feeds = section.get("feeds", [])
    if not isinstance(feeds, list):
        feeds = []
    batch_feeds: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    seen_items: set[str] = set()
    for raw_feed in feeds:
        feed = raw_feed if isinstance(raw_feed, dict) else {}
        result, found = _collect_feed(feed, _feed_state(state, _safe_text(feed.get("id", ""))), checked, section)
        batch_feeds.append(result)
        for item in found:
            if item["id"] not in seen_items:
                seen_items.add(item["id"])
                items.append(item)
        print(f"[discover] {result['id']}: {result['status']} ({result['received_count']} received, {len(found)} items)", file=sys.stderr, flush=True)
    return {
        "schema_version": 1,
        "checked_at": _iso(checked),
        "mode": "automatic_discovery",
        "feeds": batch_feeds,
        "items": items,
    }


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else default


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Collect automatic speech AI discovery metadata")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=root / "task" / "sources.json")
    parser.add_argument("--state", type=Path, default=root / "task" / "discovery_state.json")
    args = parser.parse_args(argv)
    try:
        config = _read_json(args.config, {})
        state = _read_json(args.state, {})
        batch = collect(config, state)
        _write_json(args.output, batch)
        # Collection alone cannot advance durable coverage: the publisher owns
        # validated watermarks and commits them only after successful ingestion.
        return 0
    except Exception as exc:
        print(f"discover: {_provider_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
