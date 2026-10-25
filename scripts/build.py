#!/usr/bin/env python3
"""Build the Speech AI Research Digest from the normalized data contract.

This module deliberately uses only the Python standard library.  It renders a
readable server-side baseline and then hands the frontend its four documented
substitution values when assets/page.html is present.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "data.json"
SITE_DEFAULT = "https://ahmed-ezzat20.github.io/speech-ai-research-digest"
MINIMAL_CSS = """
:root{font-family:system-ui,sans-serif;color:#17231d;background:#f5f7f2}body{max-width:1100px;margin:0 auto;padding:1rem}a{color:#176b52}header,section,article{margin:1rem 0;padding:1rem;border:1px solid #dbe4dc;border-radius:10px;background:#fff}h1,h2,h3{line-height:1.2}.meta,small{color:#526158}ul{padding-left:1.3rem}.tag{display:inline-block;margin:.15rem;padding:.15rem .4rem;border-radius:4px;background:#edf8f1;color:#176b52;font-size:.8rem}pre{white-space:pre-wrap}
""".strip()
MINIMAL_JS = """
(() => { const node=document.getElementById('digest-data'); if (!node) return; try { window.SPEECH_AI_DIGEST=JSON.parse(node.textContent); } catch (_) {} })();
""".strip()
FALLBACK_TEMPLATE = """<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Speech AI Research Digest</title><style>__CSS__</style></head>
<body><div id=\"server-rendered\">__SSR__</div><script type=\"application/json\" id=\"digest-data\">__DATA__</script><script>__JS__</script></body></html>"""
TOKEN_NAMES = ("__CSS__", "__JS__", "__DATA__", "__SSR__")


def read_data() -> dict:
    with DATA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def safe_json(value: object) -> str:
    # Prevent a data string from terminating the JSON script element.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def parse_dt(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_label(value: str) -> str:
    return parse_dt(value).astimezone().strftime("%Y-%m-%d %H:%M UTC")


def project_map(data: dict) -> dict:
    return {p["id"]: p for p in data.get("projects", [])}


def render_baseline(data: dict) -> str:
    projects = project_map(data)
    site = data["site"]
    brief = data["brief"]
    events = sorted(data.get("events", []), key=lambda x: parse_dt(x["timestamp"]), reverse=True)
    actions = data.get("actions", [])
    runs = sorted(data.get("runs", []), key=lambda x: parse_dt(x["checked_at"]), reverse=True)
    profiles = data.get("profiles", [])
    experiments = data.get("experiments", [])
    themes = brief.get("themes", [])
    out = [
        '<header><p class="meta">SOURCE-BACKED / AFRICA-CAIRO / STATIC EDITION</p>',
        f'<h1>{esc(site["title"])}</h1><p>{esc(site["description"])}</p>',
        f'<p><a href="archive.html">Archive</a> · <a href="feed.xml">Atom feed</a> · <a href="{esc(site["repo_url"])}">Source repository</a></p></header>',
        '<main>',
        f'<section><p class="meta">7-DAY BRIEF · {esc(brief.get("period_start"))} → {esc(brief.get("period_end"))}</p><h2>{esc(brief.get("headline"))}</h2><p>{esc(brief.get("summary"))}</p>',
        '<div>' + ''.join(f'<article><span class="tag">{esc(t.get("title"))}</span><p>{esc(t.get("body"))}</p><p><strong>So what:</strong> {esc(t.get("so_what"))}</p></article>' for t in themes) + '</div>',
        '<h3>Watch next</h3><ul>' + ''.join(f'<li>{esc(w.get("text"))}' + (f' <a href="{esc(w["source_url"])}">source</a>' if w.get("source_url") else '') + '</li>' for w in brief.get("watch_next", [])) + '</ul></section>',
        '<section><h2>Action board</h2><p class="meta">Persistent evidence-backed checks; evergreen items are not new alerts.</p><ul>' + ''.join(f'<li><strong>{esc(a.get("title"))}</strong> <span class="tag">{esc(a.get("category"))}</span><br>{esc(a.get("affects"))}<br><strong>Do:</strong> {esc(a.get("do"))} · <a href="{esc(a.get("source_url"))}">source</a></li>' for a in actions if not a.get("dismissed")) + '</ul></section>',
        '<section><h2>Historical milestones</h2><p class="meta">Curated dossier milestones; not source-reviewed weekly changes.</p><ol>' + ''.join(f'<li><time datetime="{esc(e["timestamp"])}">{esc(e["timestamp"][:10])}</time> · <strong>{esc(e["title"])}</strong> <span class="tag">{esc(e["category"])}</span><br>{esc(e["summary"])} · <a href="{esc(e["source_url"])}">source</a></li>' for e in events) + '</ol></section>',
        '<section><h2>Source-reviewed checks</h2>' + ''.join(f'<article><h3><a href="{esc(r.get("url"))}">{esc(r.get("headline"))}</a></h3><p class="meta">{esc(r.get("checked_at"))} · {esc(r.get("status"))}</p><p>{esc("; ".join(r.get("failures", [])) if r.get("failures") else "No failures recorded.")}</p></article>' for r in runs) + '</section>',
        '<section><h2>Research profiles</h2>' + ''.join(f'<details><summary><strong>{esc(p["name"])}</strong> · {esc(p["category"])} — {esc(p["headline"])}</summary><p>{esc(p["summary"])}</p><p><strong>Build takeaway:</strong> {esc(p["buildTakeaway"])}</p><p><strong>Caveat:</strong> {esc(p["caveat"])}</p></details>' for p in profiles) + '</section>',
        '<section><h2>Experiments</h2>' + ''.join(f'<details><summary><strong>{esc(x["title"])}</strong> · {esc(x["subtitle"])}</summary><p>{esc(x["hypothesis"])}</p><p><strong>Metrics:</strong> {esc(x["metrics"])}</p><ol>' + ''.join(f'<li>{esc(step)}</li>' for step in x["steps"]) + f'</ol><p><strong>Deliverable:</strong> {esc(x["deliverable"])}</p></details>' for x in experiments) + '</section>',
        '</main><footer><p class="meta">Generated from normalized DATA schema v1. Schedule enabled: ' + esc(site.get("schedule", {}).get("enabled")) + '.</p></footer>'
    ]
    return "".join(out)


def standalone(title: str, body: str, data: dict | None = None) -> str:
    payload = safe_json(data) if data is not None else "{}"
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{MINIMAL_CSS}</style></head><body>{body}<script type="application/json" id="digest-data">{payload}</script></body></html>'


def render_run_page(data: dict, run: dict) -> str:
    events = [e for e in data.get("events", []) if e.get("id") in set(run.get("event_ids", []))]
    failures = ''.join(f'<li>{esc(f)}</li>' for f in run.get('failures', [])) or '<li>None recorded.</li>'
    body = f'<p><a href="../index.html">Home</a> · <a href="../archive.html">Archive</a></p><h1>{esc(run["headline"])}</h1><p class="meta">Checked at {esc(run["checked_at"])} · status {esc(run["status"])}</p><h2>Reviewed events</h2><ol>' + ''.join(f'<li><strong>{esc(e["title"])}</strong> · {esc(e["timestamp"])}<p>{esc(e["summary"])}</p><a href="{esc(e["source_url"])}">Primary source</a></li>' for e in events) + ('<li>No reviewed events attached to this run.</li>' if not events else '') + f'</ol><h2>Failures</h2><ul>{failures}</ul>'
    return standalone(f'Check · {run["id"]}', body, run)


def render_brief_page(brief: dict) -> str:
    body = f'<p><a href="../index.html">Home</a> · <a href="../archive.html">Archive</a></p><h1>{esc(brief.get("headline"))}</h1><p class="meta">{esc(brief.get("period_start"))} → {esc(brief.get("period_end"))}</p><p>{esc(brief.get("summary"))}</p>' + ''.join(f'<article><h2>{esc(t.get("title"))}</h2><p>{esc(t.get("body"))}</p><p><strong>So what:</strong> {esc(t.get("so_what"))}</p></article>' for t in brief.get('themes', []))
    return standalone(f'Weekly snapshot · {brief.get("id")}', body, brief)


def render_archive(data: dict) -> str:
    runs = sorted(data.get("runs", []), key=lambda x: parse_dt(x["checked_at"]), reverse=True)
    snaps = sorted(data.get("snapshots", []), key=lambda x: parse_dt(x["generated_at"]), reverse=True)
    body = '<p><a href="index.html">Home</a> · <a href="feed.xml">Atom feed</a></p><h1>Archive</h1><h2>Checks</h2><ul>' + ''.join(f'<li><a href="{esc(r["url"])}">{esc(r["checked_at"])} · {esc(r["headline"])}</a> <span class="tag">{esc(r["status"])}</span></li>' for r in runs) + '</ul><h2>Weekly snapshots</h2><ul>' + ''.join(f'<li><a href="{esc(s["url"])}">{esc(s.get("period_start"))} → {esc(s.get("period_end"))}</a> · {esc(s.get("headline"))}</li>' for s in snaps) + '</ul>'
    return standalone('Archive · Speech AI Research Digest', body, {'runs':runs,'snapshots':snaps})


def feed(data: dict) -> str:
    site = data["site"]
    base = site.get("url", SITE_DEFAULT).rstrip('/') + '/'
    entries = []
    for run in data.get('runs', []):
        if run.get('status') in ('quiet', 'setup') or run.get('status') == 'quiet':
            continue
        entries.append((parse_dt(run['checked_at']), f'{base}{run["url"]}', run['headline'], run.get('headline',''), run['checked_at']))
    for snap in data.get('snapshots', []):
        entries.append((parse_dt(snap['generated_at']), f'{base}{snap["url"]}', f'Weekly snapshot · {snap.get("period_start")} → {snap.get("period_end")}', snap.get('summary',''), snap['generated_at']))
    entries.sort(key=lambda x:x[0], reverse=True)
    entries = entries[:20]
    updated = entries[0][4] if entries else site.get('generated_at')
    parts = ['<?xml version="1.0" encoding="utf-8"?>','<feed xmlns="http://www.w3.org/2005/Atom">',f'<title>{esc(site["title"])}</title>',f'<subtitle>{esc(site["description"])}</subtitle>',f'<id>{esc(base)}</id>',f'<link href="{esc(base)}" rel="alternate"/>',f'<link href="{esc(base)}feed.xml" rel="self"/>',f'<updated>{esc(updated)}</updated>',f'<author><name>{esc(site["title"])}</name></author>']
    for dt, url, title, summary, stamp in entries:
        parts += [ '<entry>', f'<id>{esc(url)}</id>', f'<title>{esc(title)}</title>', f'<updated>{esc(stamp)}</updated>', f'<link href="{esc(url)}"/>', f'<summary>{esc(summary)}</summary>', '</entry>' ]
    parts.append('</feed>')
    return '\n'.join(parts) + '\n'


def load_template() -> tuple[str, str, str]:
    page = ROOT / 'assets' / 'page.html'
    css = ROOT / 'assets' / 'style.css'
    js = ROOT / 'assets' / 'app.js'
    template = page.read_text(encoding='utf-8') if page.exists() else FALLBACK_TEMPLATE
    css_text = css.read_text(encoding='utf-8') if css.exists() else MINIMAL_CSS
    js_text = js.read_text(encoding='utf-8') if js.exists() else MINIMAL_JS
    return template, css_text, js_text


def build() -> None:
    data = read_data()
    template, css, js = load_template()
    ssr = render_baseline(data)
    replacements = {'__CSS__':css, '__JS__':js, '__DATA__':safe_json(data), '__SSR__':ssr}
    for token in TOKEN_NAMES:
        if token not in template:
            raise ValueError(f'frontend template missing required token {token}')
        template = template.replace(token, replacements[token])
    if any(token in template for token in TOKEN_NAMES):
        raise ValueError('generated index contains unresolved template tokens')
    (ROOT / 'index.html').write_text(template, encoding='utf-8')
    (ROOT / 'portable.html').write_text(standalone(data['site']['title'], ssr, data), encoding='utf-8')
    (ROOT / 'archive.html').write_text(render_archive(data), encoding='utf-8')
    for directory in (ROOT/'digests', ROOT/'briefs'):
        directory.mkdir(exist_ok=True)
    for run in data.get('runs', []):
        (ROOT / run['url']).write_text(render_run_page(data, run), encoding='utf-8')
    for snapshot in data.get('snapshots', []):
        (ROOT / snapshot['url']).write_text(render_brief_page(snapshot), encoding='utf-8')
    (ROOT / 'feed.xml').write_text(feed(data), encoding='utf-8')
    sources_path = ROOT / 'task' / 'sources.json'
    if sources_path.exists():
        config = json.loads(sources_path.read_text(encoding='utf-8'))
        actual_runs = [run for run in data.get('runs',[]) if run.get('status') != 'setup']
        latest = max(actual_runs, key=lambda run: parse_dt(run['checked_at']), default=None)
        state = {'timezone':data['site']['timezone'], 'schedule':data['site'].get('schedule',{}), 'last_run':latest['checked_at'] if latest else None, 'last_entry_file':latest['url'] if latest else None, 'last_snapshot':max((item['generated_at'] for item in data.get('snapshots',[])),default=None), 'sources':{source['id']:{key:source.get(key) for key in ('cursor','last_checked_at','last_status','last_run')} for source in config.get('sources',[])}}
        (ROOT / 'task' / 'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(ROOT), help='publish repository root')
    args = parser.parse_args()
    ROOT = Path(args.root).resolve()
    DATA_PATH = ROOT / 'data' / 'data.json'
    build()
    print(f'Built static digest from {DATA_PATH}')
