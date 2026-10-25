# Speech AI Research Digest

**Live site:** https://ahmed-ezzat20.github.io/speech-ai-research-digest/

A compact static digest for source-backed Arabic/Egyptian speech-AI research: model and dataset milestones, training and licensing cautions, competitor evidence, and buildable experiments. The backend is independently authored Python standard-library code; it does not copy the inspected reference implementation or require a framework build.

## Build

From the repository root:

```bash
python3 scripts/build.py
python3 -m unittest discover -s tests -v
```

The builder reads the normalized aggregate at `data/data.json`, then loads `assets/page.html`, `assets/style.css`, and `assets/app.js` when the frontend-owned files are present. The page template must contain `__CSS__`, `__JS__`, `__DATA__`, and `__SSR__`. Before those assets arrive, the builder uses a small readable fallback template; it never leaves unresolved tokens in generated output.

Generated backend-owned outputs are `index.html`, `portable.html`, `archive.html`, `feed.xml`, `digests/*.html`, and `briefs/*.html`. The feed uses absolute site URLs, XML-safe escaping, the newest 20 non-quiet reviewed runs, and actual weekly snapshots. Setup and quiet checks remain visible in the archive but are not presented as news feed entries.

## Reviewed update workflow

The owner or scheduled agent must read `task/PROMPT.md` and the exact primary URLs in `task/sources.json`, review source meaning, and prepare a public JSON payload. No credentials or unreviewed candidates belong in the repository. Validate without writing first:

```bash
python3 scripts/update.py /path/to/reviewed.json --validate-only
python3 scripts/update.py /path/to/reviewed.json
```

The updater strictly validates public URLs, project/category matches, ISO timestamps, event types, stable IDs, source evidence, and run permalinks. Identical replayed event/action/run IDs are idempotent; conflicting duplicates are rejected. Runs, events, actions, headlines, and snapshots are append-only. A quiet run is allowed only when every configured source succeeded with no reviewed event; any failure or incomplete coverage must be `partial`. Failed source cursors never advance. The rolling seven-day brief is rebuilt from actual event timestamps using Cairo calendar grouping, and a snapshot is added only when the previous actual snapshot is at least seven days old. Writes are atomic before generated pages are rebuilt.

Example quiet payload shape (fill the timestamp and source state from a real complete check; this is not a live check):

```json
{
  "run": {
    "id": "check-20261025T220000Z",
    "checked_at": "2026-10-25T19:00:00Z",
    "status": "quiet",
    "headline": "No new reviewed source changes.",
    "event_ids": [],
    "sources_checked": ["habibi", "f5tts", "casablanca", "qwen3tts", "whisper", "openaiaudio"],
    "failures": [],
    "url": "digests/check-20261025T220000Z.html"
  },
  "events": [],
  "actions": [],
  "brief": {
    "headline": "No new source-reviewed weekly changes.",
    "summary": "All configured sources were checked successfully; no relevant reviewed event was found.",
    "themes": [],
    "watch_next": []
  },
  "source_state": {
    "habibi": {"status": "checked", "cursor": null}
  }
}
```

The example is illustrative only; expand its source_state to include every sources_checked ID. A quiet payload must include complete source state and real evidence from the actual run. `task/PROMPT.md` is the operational source of truth.

## Seed and schedule status

The initial aggregate contains six full verified profiles, six historical curated milestone events, three existing model-building experiments, and four evergreen training cautions. It explicitly contains no fake recent checks, no invented seven-day activity, and no claim of a live source monitor. The setup/import run and baseline brief are labelled accordingly. Native Manus scheduling was confirmed active on 25 October 2026 at **08:00 and 22:00 Africa/Cairo**. The timezone follows Cairo daylight-saving rules. The first scheduled source-reviewed execution is still pending; activation is not proof that a source check has already completed. The header keeps setup/import distinct from an actual check. Recurring executions use Manus task credits.

The published site is designed for plain static hosting from the repository root. The source site URL and repository URL are stored in the normalized `site` object; publication, Git operations, Pages configuration, Preview, and schedule activation are owner-controlled.

## Reference and implementation

Inspired by [Ibrahim Sobh’s Coding Agents Digest](https://github.com/IbrahimSobh/coding-agents-digest). This independently authored implementation recreates its activity, brief, action, timeline, archive, feed, and scheduled-agent patterns with Speech AI research data. The reviewed reference has no explicit source-code reuse license, so no reference code, branding, or promotional assets were redistributed.

Future source checks are scheduled, not browser polling. No model weights or source audio are hosted here, and no third-party model API key is required. Add a source deliberately by extending the project/profile registry and task/sources.json; event citations must remain within registered project or paper paths. task/state.json is a generated read-friendly view of current cursor/run state; task/sources.json and data/data.json are the canonical inputs.
