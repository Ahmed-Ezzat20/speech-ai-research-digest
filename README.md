# Speech AI Research Digest

**Live site:** https://ahmed-ezzat20.github.io/speech-ai-research-digest/

A static digest for automatically discovered public metadata from arXiv speech papers and Hugging Face ASR/TTS model repositories. It preserves exact primary citations and clearly separates source metadata from hypotheses; it does not claim independently reproduced quality or infer audio/training-data rights.

## Build and tests

From the repository root:

```bash
python3 scripts/build.py
python3 -m unittest discover -s tests -v
```

The builder reads `data/data.json`, then loads the frontend-owned `assets/page.html`, `assets/style.css`, and `assets/app.js`. It preserves the required `__CSS__`, `__JS__`, `__DATA__`, and `__SSR__` substitutions, escapes HTML and embedded JSON, and emits `index.html`, `portable.html`, `archive.html`, `feed.xml`, and digest/brief permalinks. Existing historical HTML files remain untouched.

## Automatic discovery and publication

The query-only registry is `task/sources.json` (schema version 2). It contains exactly three feeds:

- `arxiv-speech` — the configured arXiv speech query, category `Research`;
- `hf-asr` — Hugging Face `automatic-speech-recognition` models, category `ASR`;
- `hf-tts` — Hugging Face `text-to-speech` models, category `TTS`.

The maintained SDK collector is owned by `scripts/discover.py` and writes a bounded batch without downloading PDFs, weights, audio, or remote code:

```bash
python3 -m venv /tmp/speech-discovery-venv
/tmp/speech-discovery-venv/bin/pip install -r requirements.txt
/tmp/speech-discovery-venv/bin/python scripts/discover.py \
  --output /tmp/harvest-batch.json \
  --config task/sources.json \
  --state task/discovery_state.json
```

The publisher validates and appends one batch automatically. It has no approval queue:

```bash
python3 scripts/harvest.py --batch /tmp/harvest-batch.json --validate-only
python3 scripts/harvest.py --batch /tmp/harvest-batch.json
# With no --batch, harvest.py invokes discover.collect(config, state) once.
python3 scripts/harvest.py
```

Publisher records are append-only and immutable. Paper identity is canonical arXiv ID plus version; model identity is Hugging Face repository plus revision/SHA. Every event retains its feed ID, source URL, source timestamp and timestamp basis. Model profiles refresh to the latest repository revision by asset key. Partial or truncated coverage is never published as quiet; successful watermarks advance only after complete feed coverage, while `pending_since` protects failed windows from silently moving forward. `task/state.json` is a generated view of each query/pipeline tag and watermark.

Collection alone does not write durable completed watermarks. Only validated ingestion advances them. Re-observing the same immutable identity does not create another event or rewrite its original receipt; current model-card metadata may refresh the profile without stopping publication. Raw metadata receipts stay in `data/data.json`, while the browser embeds only the fields its views use. The landing page shows 12 profiles at first, highlights source-declared Arabic relevance without claiming dialect quality, and limits each run preview to 12 records with a complete permalink.

Run statuses are `updated`, `quiet`, or `partial`. Cairo seven-day counts are derived from actual source timestamps. Summaries, themes, access/licensing actions, and optional experiments are derived from returned metadata and are labelled accordingly. Unknown license, language, architecture, quality, and rights remain unknown. No card or repository listing is treated as proof of Arabic/Egyptian performance, released weights, training code, or reusable audio.

## Migration and schedule

The active aggregate is schema version 2 with `site.mode: automatic_discovery`; its three projects/channels derive from the feed configuration. It starts with no seeded active profiles, events, actions, or experiments. The previous curated aggregate is preserved at [`data/legacy-setup.json`](data/legacy-setup.json), and its generated historical run/snapshot HTML remains archive material rather than current activity.

The single Manus schedule is owner-confirmed active for Sunday 07:00 Africa/Cairo. The confirmation metadata is recorded in `task/sources.json` and `data/data.json`. The first actual harvest on 25 October 2026 was partial at the initial record cap; a subsequent expanded scan completed all three query windows. The ledger records 91 paper-version records and 1,032 model-revision records, not an independently verified count of new weight releases. Scheduled collection, validation, ingestion and publication require no candidate PR or per-item owner approval. No custom timer, hosted backend, browser polling, or provider key is used.

`scripts/harvest.py` is the only active ingestion path. `scripts/discover.py` collects API metadata; `scripts/build.py` renders stored discoveries. `task/PROMPT.md` is the operating guide for automatic scheduled collection and publication.

## Maintained discovery clients

Metadata collection uses [arxiv.py](https://github.com/lukasschwab/arxiv.py) and the [official Hugging Face Hub client](https://github.com/huggingface/huggingface_hub), pinned in `requirements.txt`. Public metadata queries require no model-provider API key. Native Manus scheduled execution uses task credits. Query limits are safety bounds: if they are reached before the date boundary, coverage is explicitly partial, not a claim that the whole Hub was searched.
