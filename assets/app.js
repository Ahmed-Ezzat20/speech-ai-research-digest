(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const app = $("#app");
  const dataNode = $("#digest-data");
  const fallback = { schema_version: 1, site: { title: "Speech AI Research Digest", timezone: "Africa/Cairo", schedule: { enabled: false } }, projects: [], profiles: [], events: [], actions: [], runs: [], brief: {}, headlines: [], snapshots: [], experiments: [] };
  let data = fallback;
  let saved = readSet("speech-digest-bookmarks");
  let dismissed = readSet("speech-digest-actions-dismissed");
  let chart = { range: "all", mode: "cumulative", shape: "smooth", hidden: new Set() };
  let board = { category: "all", showAll: false, showDismissed: false };
  let timeline = { topic: "all", project: "all", query: "" };

  function readSet(key) {
    try { const value = JSON.parse(localStorage.getItem(key) || "[]"); return new Set(Array.isArray(value) ? value.map(String) : []); } catch (_) { return new Set(); }
  }
  function writeSet(key, set) { try { localStorage.setItem(key, JSON.stringify([...set])); } catch (_) { /* storage can be unavailable */ } }
  function h(value) { return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])); }
  function url(value) {
    const raw = String(value || "").trim();
    if (/^(https?:\/\/|mailto:|\/(?!\/)|\.\/|\.\.\/|#)/i.test(raw) || /^(archive\.html|feed\.xml|(?:digests|briefs)\/[A-Za-z0-9._:-]+\.html)$/.test(raw)) return h(raw);
    return "#";
  }
  function color(value, fallbackColor = "#246b50") { return /^#[0-9a-f]{3,8}$/i.test(String(value || "")) ? value : fallbackColor; }
  function validDate(value) { const date = value ? new Date(value) : null; return date && !Number.isNaN(date.getTime()) ? date : null; }
  function isoDay(value) { const d = validDate(value); return d ? d.toISOString().slice(0, 10) : ""; }
  function addDays(day, amount) { const d = new Date(`${day}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + amount); return d.toISOString().slice(0, 10); }
  function dateLabel(day, options = {}) { const d = validDate(`${day}T12:00:00Z`); return d ? new Intl.DateTimeFormat("en", { timeZone: "UTC", month: options.month || "short", day: "numeric", year: options.year ? "numeric" : undefined, weekday: options.weekday }).format(d) : "Undated"; }
  function localLabel(value) { const d = validDate(value); return d ? new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(d) : "Unknown time"; }
  function cairoDay(value) { const d = validDate(value); if (!d) return "unknown"; const parts = new Intl.DateTimeFormat("en-CA", { timeZone: data.site?.timezone || "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(d); return `${parts.find(p => p.type === "year")?.value}-${parts.find(p => p.type === "month")?.value}-${parts.find(p => p.type === "day")?.value}`; }
  function project(id) { return data.projects.find(item => item.id === id) || { id, name: id || "Unknown source", color: "#246b50", category: "Research", sources: [] }; }
  function eventsForRun(run) { const ids = new Set(Array.isArray(run.event_ids) ? run.event_ids : []); return data.events.filter(event => ids.has(event.id)); }
  function link(label, href, extra = "") { const safe = url(href); return safe === "#" ? "" : `<a href="${safe}" target="_blank" rel="noopener noreferrer" ${extra}>${h(label)}</a>`; }

  function initTheme() {
    let theme = "light";
    try { theme = localStorage.getItem("speech-digest-theme") === "dark" ? "dark" : "light"; } catch (_) { /* default */ }
    document.documentElement.dataset.theme = theme;
    const button = $("#theme-toggle");
    if (button) { button.setAttribute("aria-pressed", String(theme === "dark")); button.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} theme`); }
  }
  function toggleTheme() {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("speech-digest-theme", theme); } catch (_) { /* no persistence */ }
    const button = $("#theme-toggle");
    if (button) { button.setAttribute("aria-pressed", String(theme === "dark")); button.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} theme`); }
    if ($("#history-svg")) drawChart();
  }
  function setSiteMeta() {
    const repo = data.site?.repo_url;
    [$("#repo-link"), $("#footer-repo")].forEach(node => { if (node && repo) { node.href = url(repo); node.target = "_blank"; node.rel = "noopener noreferrer"; } });
    const freshness = $("#freshness");
    const schedule = data.site?.schedule || {};
    const generated = validDate(data.site?.generated_at);
    const latestRun = data.runs.filter(run => run.status !== "setup" && run.sources_checked?.length).map(run => validDate(run.checked_at)).filter(Boolean).sort((a, b) => b - a)[0];
    if (!freshness) return;
    if (schedule.enabled) {
      const last = latestRun;
      freshness.textContent = last ? `Last checked ${localLabel(last)}` : "Schedule enabled · no recorded check";
      if (Array.isArray(schedule.times) && schedule.times.length) {
        const next = nextCheck(new Date(), schedule.times);
        if (next) freshness.textContent += ` · scheduled next in about ${Math.max(1, Math.ceil((next - Date.now()) / 3600000))} h`;
      }
    } else {
      freshness.textContent = generated ? `Snapshot generated ${localLabel(generated)}` : "No active schedule";
    }
    const footer = $("#footer-meta");
    if (footer) footer.textContent = data.site?.timezone ? `Static page · timezone ${data.site.timezone}` : "Static page · source dates preserved";
  }
  function nextCheck(now, times) {
    const formatter = new Intl.DateTimeFormat("en-CA", {timeZone: data.site?.timezone || "Africa/Cairo", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit", hourCycle:"h23"});
    const candidates = [];
    for (let offset = 0; offset < 3; offset += 1) {
      const day = addDays(cairoDay(now), offset);
      for (const time of times) {
        if (!/^\d{2}:\d{2}$/.test(time)) continue;
        const desired = Date.parse(`${day}T${time}:00Z`);
        let instant = desired;
        for (let pass = 0; pass < 3; pass += 1) {
          const parts = Object.fromEntries(formatter.formatToParts(new Date(instant)).map(part => [part.type, part.value]));
          const wall = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
          instant += desired - wall;
        }
        if (instant > now.getTime()) candidates.push(instant);
      }
    }
    return candidates.length ? new Date(Math.min(...candidates)) : null;
  }
  function weeklyEvents() {
    const start = validDate(data.brief?.period_start);
    const end = validDate(data.brief?.period_end);
    if (!start || !end) return [];
    return data.events.filter(event => {const stamp = validDate(event.timestamp); return stamp && stamp >= start && stamp < end;});
  }
  function failureText(failures) {
    return (failures || []).map(item => typeof item === "string" ? item : `${item.source_id || item.project_id || item.id || "Source"}: ${item.message || item.reason || item.error || "not checked"}`).join("; ");
  }
  function currentActions() {
    const today = cairoDay(new Date());
    return data.actions.filter(item => item.deadline ? String(item.deadline).slice(0,10) >= today : item.evergreen === true || (cairoDay(item.timestamp) >= addDays(today,-6) && cairoDay(item.timestamp) <= today));
  }

  function activitySection() {
    const events = data.events.filter(event => validDate(event.timestamp) && project(event.project_id));
    const categories = ["all", ...new Set(data.projects.map(item => item.category).filter(Boolean))];
    const controls = categories.map(category => `<button type="button" data-chart-category="${h(category)}" aria-pressed="${category === "all" ? "true" : "false"}">${h(category === "all" ? "All sources" : category)}</button>`).join("");
    return `<section class="section" id="history" aria-labelledby="history-title"><div class="section-head"><h2 id="history-title">Research activity</h2><p>Tracked updates · curated history included</p></div><p class="section-copy">Counts are events, not importance. Historical seed milestones are labelled as curated history rather than current releases.</p><div class="controls"><div class="segmented" role="group" aria-label="Activity time range"><button type="button" data-chart-range="1d" aria-pressed="${chart.range === "1d"}">1 Day</button><button type="button" data-chart-range="1w" aria-pressed="${chart.range === "1w"}">1 Week</button><button type="button" data-chart-range="1m" aria-pressed="${chart.range === "1m"}">1 Month</button><button type="button" data-chart-range="all" aria-pressed="${chart.range === "all"}">All</button></div><div class="segmented chart-shape-controls" role="group" aria-label="Activity measure"><button type="button" data-chart-mode="cumulative" aria-pressed="${chart.mode === "cumulative"}">Cumulative</button><button type="button" data-chart-mode="daily" aria-pressed="${chart.mode === "daily"}">Daily</button></div><div class="segmented chart-shape-controls" role="group" aria-label="Line shape"><button type="button" data-chart-shape="smooth" aria-pressed="${chart.shape === "smooth"}">Smooth</button><button type="button" data-chart-shape="steps" aria-pressed="${chart.shape === "steps"}">Steps</button></div></div><div class="chart-wrap" id="chart-wrap"><svg id="history-svg" role="img" aria-label="Speech AI research events over time" viewBox="0 0 900 275"></svg><div id="chart-tooltip" class="chart-tooltip" hidden></div></div><div id="chart-legend" class="chart-legend" aria-label="Project series"></div><p class="chart-note">Zero-filled calendar days keep quiet periods visible. One-day view becomes a horizontal event-count bar.</p><details class="accessible-data"><summary>Show accessible activity table</summary><div id="activity-table-wrap"></div></details></section>`;
  }

  function renderBrief() {
    const end = isoDay(data.site?.generated_at);
    const recent = weeklyEvents();
    const counts = new Map(data.projects.map(item => [item.id, 0]));
    recent.forEach(event => counts.set(event.project_id, (counts.get(event.project_id) || 0) + 1));
    const active = [...counts.values()].filter(Boolean).length;
    const max = Math.max(1, ...counts.values());
    const bars = data.projects.map(item => `<div class="project-bar"><span>${h(item.name)}</span><span class="bar-track"><span class="bar-fill" style="width:${Math.round(((counts.get(item.id) || 0) / max) * 100)}%;background:${color(item.color)}"></span></span><b>${counts.get(item.id) || 0}</b></div>`).join("");
    const themes = Array.isArray(data.brief?.themes) ? data.brief.themes : [];
    const cards = themes.slice(0, 3).map((theme, index) => `<article class="brief-card"><small>${h(theme.tag || `Theme ${String(index + 1).padStart(2, "0")}`)}</small><h4>${h(theme.title || "Research theme")}</h4><p>${h(theme.body || theme.facts || "Source-backed context for the next build decision.")}</p>${theme.so_what ? `<p><strong>Why it matters:</strong> ${h(theme.so_what)}</p>` : ""}<div class="theme-sources">${(theme.source_urls || []).map((source, i) => link(`Source ${i + 1} ↗`, source)).join(" · ")}</div></article>`).join("");
    const watch = (Array.isArray(data.brief?.watch_next) ? data.brief.watch_next : []).slice(0, 2).map(item => `<p>${h(item.text || item)} ${item.source_url ? link("Source ↗", item.source_url) : ""}</p>`).join("");
    const history = [...(Array.isArray(data.headlines) ? data.headlines : [])].sort((a, b) => String(b.checked_at || b.updated || "").localeCompare(String(a.checked_at || a.updated || ""))).slice(0, 12).map(item => `<li><time datetime="${h(item.checked_at || item.updated || "")}">${h(localLabel(item.checked_at || item.updated))}</time> · ${h(item.headline || "Untitled brief")}</li>`).join("");
    const state = end ? (recent.length ? "verified events in the current window" : "no new source-reviewed weekly events yet") : "window unavailable until a generated timestamp is supplied";
    return `<section class="section brief" id="brief" aria-labelledby="brief-title"><div class="section-head"><h2 id="brief-title">Rolling 7-day brief</h2><p>${h(state)}</p></div><h3>${h(data.brief?.headline || "A quiet baseline is still useful evidence.")}</h3><p class="brief-summary">${h(data.brief?.summary || "This snapshot separates verified source activity from orientation themes and open questions.")}</p><div class="brief-stats"><div class="brief-stat"><b>${recent.length}</b><span>verified events in window</span></div><div class="brief-stat"><b>${active}</b><span>active projects</span></div><div class="brief-stat"><b>${currentActions().filter(item => !item.dismissed && !dismissed.has(String(item.id))).length}</b><span>open checks</span></div></div><div class="project-bars">${bars || `<p class="empty">No project series supplied.</p>`}</div><div class="brief-grid">${cards || `<p class="empty">No themes supplied for this snapshot.</p>`}</div><div class="brief-bottom"><div><h4>WHY IT MATTERS</h4><p>${h(recent.length ? "The current window is derived from dated events above; inspect each source before treating a count as a decision." : "The archive may contain useful historical context, but this view does not promote old seed milestones into recent news.")}</p></div><div><h4>WATCH NEXT</h4>${watch || `<p>No watch item supplied.</p>`}</div></div><details class="headline-history"><summary>Show append-only headline history</summary><ul>${history || "<li>No prior headlines supplied.</li>"}</ul></details></section>`;
  }

  function renderActions() {
    const today = cairoDay(new Date());
    const all = currentActions().filter(item => { const id = String(item.id || ""); const localDismissed = dismissed.has(id) || item.dismissed === true; if (board.showDismissed ? !localDismissed : localDismissed) return false; if (item.deadline) return String(item.deadline).slice(0, 10) >= today; return true; }).sort((a, b) => { const ad = a.deadline ? String(a.deadline) : "9999-99-99"; const bd = b.deadline ? String(b.deadline) : "9999-99-99"; return ad === bd ? String(b.timestamp || "").localeCompare(String(a.timestamp || "")) : ad.localeCompare(bd); });
    const categories = [...new Set(all.map(item => item.category).filter(Boolean))];
    const filtered = board.category === "all" ? all : all.filter(item => item.category === board.category);
    const visible = board.showAll ? filtered : filtered.slice(0, 6);
    const cards = visible.map(item => `<article class="action-card"><div class="action-meta"><span class="category">${h(item.category || "evidence")}</span><span>${h(item.deadline ? `Deadline ${dateLabel(String(item.deadline).slice(0, 10), { year: true })}` : item.evergreen ? "Evergreen check" : `Flagged ${dateLabel(cairoDay(item.timestamp), {year:true})}`)}</span></div><h3>${h(item.title || "Research check")}</h3><dl><dt>Affects</dt><dd>${h(item.affects || "Review the source before building.")}</dd><dt>Do this</dt><dd>${h(item.do || "Open the evidence and record the decision.")}</dd></dl><div class="action-footer">${item.source_url ? link("Open source ↗", item.source_url) : "<span></span>"}<button class="dismiss" type="button" data-action-dismiss="${h(item.id)}" ${item.dismissed ? "disabled" : ""}>${item.dismissed ? "Dismissed in repository" : board.showDismissed ? "Restore" : "Dismiss locally"}</button></div></article>`).join("");
    const dismissedCount = data.actions.filter(item => dismissed.has(String(item.id)) || item.dismissed === true).length;
    const categoryOptions = ["all", ...categories].map(category => `<option value="${h(category)}" ${board.category === category ? "selected" : ""}>${h(category === "all" ? "All categories" : category)}</option>`).join("");
    return `<section class="section" id="actions" aria-labelledby="actions-title"><div class="section-head"><h2 id="actions-title">Before you build</h2><p>${filtered.length} ${board.showDismissed ? "dismissed" : "open"} checks</p></div><p class="section-copy">Evidence-backed checks for Arabic and Egyptian speech work. Seeded evergreen checks are not alerts.</p><div class="filter-row"><label class="visually-hidden" for="action-category">Action category</label><select class="select" id="action-category">${categoryOptions}</select><button class="control" type="button" data-action-dismissed aria-pressed="${board.showDismissed}">${board.showDismissed ? "Hide dismissed" : `Show dismissed (${dismissedCount})`}</button></div><div class="action-grid">${cards || `<p class="empty">No checks match this view.</p>`}</div>${filtered.length > 6 && !board.showAll ? `<button class="button show-more" type="button" data-action-more>Show all ${filtered.length} checks</button>` : ""}</section>`;
  }

  function timelineMatches(run) {
    const events = eventsForRun(run);
    const topicOk = timeline.topic === "all" || events.some(event => event.category === timeline.topic) || (run.sources_checked || []).some(id => project(id).category === timeline.topic);
    const projectOk = timeline.project === "all" || events.some(event => event.project_id === timeline.project) || (run.sources_checked || []).includes(timeline.project);
    const text = `${run.headline || ""} ${events.map(event => `${event.title} ${event.summary}`).join(" ")}`.toLowerCase();
    return topicOk && projectOk && (!timeline.query || text.includes(timeline.query.toLowerCase()));
  }
  function renderRun(run, open = false) {
    const events = eventsForRun(run);
    const projects = [...new Set(events.map(event => project(event.project_id)))];
    const runColor = projects.length === 1 ? color(projects[0].color) : (projects.length > 1 ? color(projects[0].color) : "#8b9690");
    const eventHtml = events.map(event => `<div class="run-event"><strong>${h(event.title || "Untitled event")}</strong><p>${h(event.summary || "")}</p><div class="event-links">${link(`${h(project(event.project_id).name)} · source ↗`, event.source_url)}${event.action?.source_url ? link("Action evidence ↗", event.action.source_url) : ""}</div></div>`).join("");
    const status = run.status || "setup";
    const body = status === "quiet" && !run.failures?.length ? `<p class="quiet-line">No reviewed updates in this check. ${run.sources_checked?.length ? `${h(run.sources_checked.length)} sources checked.` : "Source coverage not supplied."}</p>` : `${eventHtml || `<p class="quiet-line">No event details supplied for this run.</p>`}${Array.isArray(run.failures) && run.failures.length ? `<p class="quiet-line"><strong>Not checked:</strong> ${h(failureText(run.failures))}</p>` : ""}`;
    return `<details class="run ${h(status)}" style="--run-color:${runColor}" ${open ? "open" : ""}><summary><span class="run-head"><time datetime="${h(run.checked_at || "")}" title="${h(localLabel(run.checked_at))}">${h(localLabel(run.checked_at))}</time><strong>${h(run.headline || status)}</strong><span class="run-status">${h(status)} · ${events.length} event${events.length === 1 ? "" : "s"}</span></span></summary><div class="run-body">${body}${run.url ? `<div class="event-links">${link("Open run permalink ↗", run.url)}</div>` : ""}</div></details>`;
  }
  function renderQuietGroup(group) { const first = group[0], last = group[group.length - 1]; return `<div class="run quiet"><div class="run-head"><time datetime="${h(first.checked_at || "")}" title="${h(localLabel(first.checked_at))}">${group.length > 1 ? `${h(localLabel(first.checked_at))} → ${h(localLabel(last.checked_at))}` : h(localLabel(first.checked_at))}</time><strong>No reviewed updates</strong><span class="run-status">quiet · ${group.length} check${group.length === 1 ? "" : "s"}</span></div><p class="quiet-line">${group.some(run => run.failures?.length) ? "Some sources were not checked; this is not a clean quiet result." : "All recorded sources completed without a reviewed event."}</p></div>`; }
  function renderTimeline() {
    const runs = [...data.runs].filter(timelineMatches).sort((a, b) => String(b.checked_at || "").localeCompare(String(a.checked_at || ""))).slice(0, 120);
    const groups = []; const map = new Map();
    runs.forEach(run => { const day = cairoDay(run.checked_at) || "unknown"; if (!map.has(day)) { const group = { day, runs: [] }; map.set(day, group); groups.push(group); } map.get(day).runs.push(run); });
    const days = groups.map((group, dayIndex) => { const chunks = []; group.runs.forEach(run => { const isQuiet = run.status === "quiet" && !run.failures?.length; if (isQuiet && chunks.length && chunks[chunks.length - 1].quiet) chunks[chunks.length - 1].runs.push(run); else chunks.push({ quiet: isQuiet, runs: [run] }); }); const body = chunks.map(chunk => chunk.quiet ? renderQuietGroup(chunk.runs) : renderRun(chunk.runs[0], dayIndex === 0)).join(""); const list = `<div class="run-list">${body || `<p class="empty">No checks match these filters.</p>`}</div>`; return `<section class="timeline-day"><div class="day-heading"><h3>${h(dateLabel(group.day, { weekday: "long", year: true }))}</h3><small>${group.runs.length} run${group.runs.length === 1 ? "" : "s"}</small></div>${dayIndex === 0 ? list : `<details><summary class="day-heading"><span>Show ${group.runs.length} older run${group.runs.length === 1 ? "" : "s"}</span></summary>${list}</details>`}</section>`; }).join("");
    const topics = [...new Set(data.events.map(event => event.category).filter(Boolean))];
    const topicOptions = ["all", ...topics].map(topic => `<option value="${h(topic)}" ${timeline.topic === topic ? "selected" : ""}>${h(topic === "all" ? "All topics" : topic)}</option>`).join("");
    const projectOptions = ["all", ...data.projects.map(item => item.id)].map(id => `<option value="${h(id)}" ${timeline.project === id ? "selected" : ""}>${h(id === "all" ? "All projects" : project(id).name)}</option>`).join("");
    return `<section class="section" id="timeline" aria-labelledby="timeline-title"><div class="section-head"><h2 id="timeline-title">Daily checks</h2><p>Latest ${Math.min(120, data.runs.length)} recorded runs</p></div><p class="section-copy">Newest Cairo day is open. Older days stay collapsed; quiet checks are grouped only when contiguous and failure runs remain visible.</p><div class="filter-row"><label class="visually-hidden" for="timeline-topic">Topic filter</label><select class="select" id="timeline-topic">${topicOptions}</select><label class="visually-hidden" for="timeline-project">Project filter</label><select class="select" id="timeline-project">${projectOptions}</select><input class="search" id="timeline-search" type="search" placeholder="Search runs or events" value="${h(timeline.query)}" aria-label="Search runs or events"><span class="result-count">${runs.length} shown</span></div><div class="timeline">${days || `<p class="empty">No recorded runs supplied.</p>`}</div></section>`;
  }

  function renderProfiles() {
    const cards = data.profiles.map(profile => { const facts = Array.isArray(profile.facts) ? profile.facts : []; const links = Array.isArray(profile.links) ? profile.links : []; const field = (...keys) => keys.map(key => profile[key]).find(value => value); return `<details class="profile" id="profile-${h(profile.id)}"><summary><span class="pulse" style="background:${color(project(profile.id).color)}" aria-hidden="true"></span><strong>${h(profile.name || profile.id)}</strong><small>${h(profile.category || "Research profile")}</small></summary><div class="profile-body"><p class="profile-provenance">${h(profile.sourceDate || "Source date not supplied")}</p><h4>${h(profile.headline || "")}</h4><p>${h(profile.summary || "")}</p><dl><dt>Architecture</dt><dd>${h(field("architecture") || "Not supplied")}</dd><dt>Assets available</dt><dd>${h(field("availability", "assetAvailability") || "Not supplied")}</dd><dt>License / reuse</dt><dd>${h(field("license", "licenses") || "Not supplied")}</dd><dt>Arabic coverage</dt><dd>${h(field("languages", "arabicCoverage") || "Not established")}</dd></dl>${facts.length ? `<strong>What the source reports</strong><ul>${facts.map(fact => `<li>${h(fact)}</li>`).join("")}</ul>` : ""}${field("buildTakeaway") ? `<p><strong>Build takeaway:</strong> ${h(field("buildTakeaway"))}</p>` : ""}${field("caveat") ? `<p class="profile-caveat"><strong>Caveat:</strong> ${h(field("caveat"))}</p>` : ""}<div class="source-links">${links.map(item => link(item.label || "Source", item.url)).join("")}<button class="control" type="button" data-save="${h(profile.id)}" aria-pressed="${saved.has(String(profile.id))}">${saved.has(String(profile.id)) ? "Saved locally" : "Save profile"}</button></div></div></details>`; }).join("");
    return `<section class="section" id="profiles" aria-labelledby="profiles-title"><div class="section-head"><h2 id="profiles-title">Research profiles</h2><p>${data.profiles.length} source-grounded references</p></div><p class="section-copy">Architecture, actual assets, licenses, and Arabic evidence are kept separate so an open checkpoint is not mistaken for an open corpus.</p><div class="profile-grid">${cards || `<p class="empty">No research profiles supplied.</p>`}</div></section>`;
  }

  function renderExperiments() {
    const experiments = Array.isArray(data.experiments) ? data.experiments : []; const featured = experiments[0];
    if (!featured) return `<section class="section" id="experiments"><p class="empty">No experiments supplied for this edition.</p></section>`;
    const steps = Array.isArray(featured.steps) ? featured.steps : []; const metrics = Array.isArray(featured.metrics) ? featured.metrics : featured.metrics ? [featured.metrics] : []; const secondary = experiments.slice(1, 3).map(item => `<details><summary>${h(item.title || "Research idea")}</summary><p>${h(item.hypothesis || item.subtitle || "Hypothesis not supplied.")}</p><button class="button" type="button" data-download-experiment="${h(item.id)}">Download experiment Markdown</button></details>`).join("");
    return `<details class="section experiment" id="experiments" aria-labelledby="experiment-title"><summary class="experiment-summary"><span><p class="section-kicker">Featured Egyptian ASR experiment</p><h3 id="experiment-title">${h(featured.title || "Egyptian code-switching ASR")}</h3><p>${h(featured.subtitle || featured.hypothesis || "A reproducible test plan, not a claimed benchmark.")}</p></span><span class="experiment-open">Open plan</span></summary><div class="experiment-main"><p><strong>Hypothesis · not a reported result:</strong> ${h(featured.hypothesis || "")}</p><ol>${steps.map(step => `<li>${h(step)}</li>`).join("")}</ol><div class="metric-tags">${metrics.map(metric => `<span>${h(metric)}</span>`).join("")}</div><button class="button button-primary" type="button" data-download-experiment="${h(featured.id)}">Download experiment Markdown</button></div><div class="experiment-side"><strong>Artifact / deliverable</strong><p>${h(featured.deliverable || "Artifact not supplied.")}</p><strong>Tags</strong><p>${h((featured.tags || []).join(" · "))}</p></div>${secondary ? `<div class="secondary-experiments"><p class="section-kicker">Two more ideas</p>${secondary}</div>` : ""}</details>`;
  }

  function renderAll() {
    app.innerHTML = `${activitySection()}${renderBrief()}${renderActions()}${renderTimeline()}${renderProfiles()}${renderExperiments()}`;
    drawChart();
  }

  function chartRange() {
    const valid = data.events.map(event => cairoDay(event.timestamp)).filter(Boolean).sort(); if (!valid.length) return null;
    const start = valid[0]; const generated = cairoDay(data.site?.generated_at); const end = generated && generated > valid[valid.length - 1] ? generated : valid[valid.length - 1]; const days = chart.range === "1d" ? 1 : chart.range === "1w" ? 7 : chart.range === "1m" ? 30 : 0;
    const from = days ? addDays(end, -(days - 1)) : start;
    const clipped = from > start ? from : start; const dates = []; for (let day = clipped; day <= end; day = addDays(day, 1)) dates.push(day); return { dates, end };
  }
  function drawChart() {
    const svg = $("#history-svg"); const legend = $("#chart-legend"); const table = $("#activity-table-wrap"); if (!svg || !legend || !table) return;
    const range = chartRange(); const projects = data.projects; legend.innerHTML = projects.map(item => `<button class="legend-button" type="button" data-chart-project="${h(item.id)}" aria-pressed="${!chart.hidden.has(item.id)}"><span class="legend-swatch" style="background:${color(item.color)}"></span>${h(item.name)} <b>${chartCount(item.id, range)}</b></button>`).join("");
    if (!range || !range.dates.length) { svg.innerHTML = `<text x="450" y="135" text-anchor="middle" fill="currentColor" opacity=".55">No dated events supplied.</text>`; table.innerHTML = `<p class="empty">No activity rows are available.</p>`; return; }
    const byDay = new Map(range.dates.map(day => [day, new Map(projects.map(item => [item.id, 0]))])); const eventMap = new Map();
    data.events.forEach(event => { const day = cairoDay(event.timestamp); if (byDay.has(day) && byDay.get(day).has(event.project_id)) { byDay.get(day).set(event.project_id, byDay.get(day).get(event.project_id) + 1); if (!eventMap.has(`${day}|${event.project_id}`)) eventMap.set(`${day}|${event.project_id}`, []); eventMap.get(`${day}|${event.project_id}`).push(event); } });
    const active = projects.filter(item => !chart.hidden.has(item.id)); const width = 900; const height = 275; const pad = { left: chart.range === "1d" ? 145 : 45, right: 18, top: 18, bottom: 38 }; const innerW = width - pad.left - pad.right; const innerH = height - pad.top - pad.bottom; const max = chart.mode === "daily" && chart.range !== "1d" ? Math.max(1, ...range.dates.map(day => active.reduce((sum,item) => sum + byDay.get(day).get(item.id),0))) : Math.max(1, ...active.map(item => [...byDay.values()].reduce((sum, values) => sum + values.get(item.id), 0)));
    const textColor = getComputedStyle(document.documentElement).getPropertyValue("--faint").trim() || "#8b9690"; const grid = [0, .5, 1].map(ratio => `<line x1="${pad.left}" x2="${width - pad.right}" y1="${pad.top + innerH * ratio}" y2="${pad.top + innerH * ratio}" stroke="${textColor}" opacity=".2"/><text x="${pad.left - 8}" y="${pad.top + innerH * ratio + 4}" text-anchor="end" fill="${textColor}" font-size="10">${Math.round(max * (1 - ratio))}</text>`).join("");
    let shapes = ""; const x = index => range.dates.length === 1 ? pad.left + innerW / 2 : pad.left + (index / (range.dates.length - 1)) * innerW; const y = value => pad.top + innerH - (value / max) * innerH;
    if (chart.range === "1d") { const values = active.map(item => ({ item, value: byDay.get(range.dates[0]).get(item.id) })); const barH = Math.min(28, innerH / Math.max(1, values.length) - 6); shapes = values.map((entry, index) => { const yy = pad.top + index * (innerH / Math.max(1, values.length)) + 4; const tooltip = `${entry.item.name} · ${dateLabel(range.dates[0], { year: true })}: ${entry.value} event${entry.value === 1 ? "" : "s"}`; return `<text x="${pad.left - 10}" y="${yy + barH / 2 + 4}" text-anchor="end" fill="${textColor}" font-size="10">${h(entry.item.name)}</text><rect x="${pad.left}" y="${yy}" width="${Math.max(1, (entry.value / max) * innerW)}" height="${barH}" rx="3" fill="${color(entry.item.color)}" data-tip="${h(tooltip)}" tabindex="0"></rect>`; }).join(""); }
    else if (chart.mode === "daily") { const slot = innerW / range.dates.length; shapes = range.dates.map((day, index) => { let cursor = pad.top + innerH; return active.map(item => { const value = byDay.get(day).get(item.id); if (!value) return ""; const barH = (value / max) * innerH; cursor -= barH; const tooltip = `${dateLabel(day, { year: true })} · ${item.name}: ${value} event${value === 1 ? "" : "s"}${eventMap.has(`${day}|${item.id}`) ? ` — ${eventMap.get(`${day}|${item.id}`).map(event => event.title).join(", ")}` : ""}`; return `<rect x="${pad.left + index * slot + slot * .12}" y="${cursor}" width="${Math.max(1, slot * .76)}" height="${Math.max(0, barH)}" fill="${color(item.color)}" data-tip="${h(tooltip)}" tabindex="0"></rect>`; }).join(""); }).join(""); }
    else { shapes = active.map(item => { let total = 0; const points = range.dates.map((day, index) => { total += byDay.get(day).get(item.id); return [x(index), y(total)]; }); const path = linePath(points, chart.shape); const pointsHtml = points.map((point, index) => { const day = range.dates[index]; if (range.dates.length > 8 && !eventMap.has(`${day}|${item.id}`)) return ""; const events = eventMap.get(`${day}|${item.id}`) || []; const tooltip = `${item.name} · ${dateLabel(day, { year: true })}: ${byDay.get(day).get(item.id)} event${byDay.get(day).get(item.id) === 1 ? "" : "s"}${events.length ? ` — ${events.map(event => event.title).join(", ")}` : ""}`; return `<circle cx="${point[0]}" cy="${point[1]}" r="${range.dates.length <= 8 ? 3 : 2}" fill="${color(item.color)}" data-tip="${h(tooltip)}" tabindex="0"></circle>`; }).join(""); return `<path d="${path}" fill="none" stroke="${color(item.color)}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"></path>${pointsHtml}`; }).join(""); }
    const labels = range.dates.map((day, index) => { if (range.dates.length > 12 && index % Math.ceil(range.dates.length / 7) !== 0 && index !== range.dates.length - 1) return ""; return `<text x="${x(index)}" y="${height - 12}" text-anchor="middle" fill="${textColor}" font-size="10">${h(dateLabel(day))}</text>`; }).join("");
    svg.innerHTML = `${grid}${shapes}${labels}`; table.innerHTML = `<table class="data-table"><caption class="visually-hidden">Accessible daily event counts</caption><thead><tr><th scope="col">Date</th>${active.map(item => `<th scope="col">${h(item.name)}</th>`).join("")}<th scope="col">Total</th></tr></thead><tbody>${range.dates.map(day => { const total = active.reduce((sum,item) => sum + byDay.get(day).get(item.id),0); return `<tr><th scope="row">${h(dateLabel(day, { year: true }))}</th>${active.map(item => `<td>${byDay.get(day).get(item.id)}</td>`).join("")}<td>${total}</td></tr>`; }).join("")}</tbody></table>`;
    const modeControls = $$(".chart-shape-controls"); modeControls.forEach(node => { node.hidden = chart.range === "1d"; }); bindChartTips();
  }
  function chartCount(id, range) { if (!range) return 0; return data.events.filter(event => event.project_id === id && range.dates.includes(cairoDay(event.timestamp))).length; }
  function linePath(points, shape) {
    if (!points.length) return "";
    if (shape === "steps") return points.reduce((path, point, index) => index ? `${path} H ${point[0]} V ${point[1]}` : `M ${point[0]} ${point[1]}`, "");
    return points.reduce((path, point, index) => {
      if (!index) return `M ${point[0]} ${point[1]}`;
      const previous = points[index-1]; const mid = (previous[0]+point[0])/2;
      return `${path} C ${mid} ${previous[1]}, ${mid} ${point[1]}, ${point[0]} ${point[1]}`;
    }, "");
  }

  function bindChartTips() { const wrap = $("#chart-wrap"); const tooltip = $("#chart-tooltip"); if (!wrap || !tooltip) return; $$(`[data-tip]`, wrap).forEach(node => { const show = () => { tooltip.innerHTML = h(node.dataset.tip); tooltip.hidden = false; const a = node.getBoundingClientRect(); const b = wrap.getBoundingClientRect(); tooltip.style.left = `${Math.max(4, Math.min(b.width - 270, a.left - b.left + a.width / 2))}px`; tooltip.style.top = `${Math.max(2, a.top - b.top - 46)}px`; }; const hide = () => { tooltip.hidden = true; }; node.addEventListener("mouseenter", show); node.addEventListener("focus", show); node.addEventListener("mouseleave", hide); node.addEventListener("blur", hide); }); }

  function markdownDigest() { const lines = [`# ${data.site?.title || "Speech AI Research Digest"}`, "", data.brief?.headline || "Research snapshot", "", data.brief?.summary || "", "", "## Rolling seven-day counts"]; const end = isoDay(data.site?.generated_at); data.projects.forEach(item => lines.push(`- ${item.name}: ${end ? weeklyEvents().filter(event => event.project_id === item.id).length : 0}`)); lines.push("", "## Selected verified activity (includes curated historical milestones)"); data.events.slice().sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp))).slice(0, 30).forEach(event => lines.push(`- ${isoDay(event.timestamp) || "undated"} — ${event.title} (${project(event.project_id).name}) — ${event.source_url || "source not supplied"}`)); return lines.join("\n"); }
  function markdownExperiment(item) { return [`# ${item.title || "Speech AI experiment"}`, "", item.subtitle || item.hypothesis || "", "", "## Steps", ...(item.steps || []).map((step, index) => `${index + 1}. ${step}`), "", "## Metrics", ...(Array.isArray(item.metrics) ? item.metrics : item.metrics ? [item.metrics] : []).map(metric => `- ${metric}`), "", `## Deliverable\n${item.deliverable || "Not supplied."}`].join("\n"); }
  function download(name, content) { const blob = new Blob([content], { type: "text/markdown;charset=utf-8" }); const href = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = href; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(href), 1000); }

  document.addEventListener("click", event => {
    const target = event.target.closest("button, a"); if (!target) return;
    if (target.id === "theme-toggle") return toggleTheme();
    if (target.matches("[data-chart-range]")) { chart.range = target.dataset.chartRange; $$("[data-chart-range]").forEach(node => node.setAttribute("aria-pressed", String(node.dataset.chartRange === chart.range))); drawChart(); return; }
    if (target.matches("[data-chart-mode]")) { chart.mode = target.dataset.chartMode; $$("[data-chart-mode]").forEach(node => node.setAttribute("aria-pressed", String(node.dataset.chartMode === chart.mode))); drawChart(); return; }
    if (target.matches("[data-chart-shape]")) { chart.shape = target.dataset.chartShape; $$("[data-chart-shape]").forEach(node => node.setAttribute("aria-pressed", String(node.dataset.chartShape === chart.shape))); drawChart(); return; }
    if (target.matches("[data-chart-project]")) { const id = target.dataset.chartProject; chart.hidden.has(id) ? chart.hidden.delete(id) : chart.hidden.add(id); drawChart(); return; }
    if (target.matches("[data-action-dismissed]")) { board.showDismissed = !board.showDismissed; renderAll(); return; }
    if (target.matches("[data-action-more]")) { board.showAll = true; renderAll(); return; }
    if (target.matches("[data-action-dismiss]")) { const id = String(target.dataset.actionDismiss); if (board.showDismissed) dismissed.delete(id); else dismissed.add(id); writeSet("speech-digest-actions-dismissed", dismissed); renderAll(); return; }
    if (target.matches("[data-save]")) { const id = String(target.dataset.save); saved.has(id) ? saved.delete(id) : saved.add(id); writeSet("speech-digest-bookmarks", saved); renderAll(); return; }
    if (target.matches("[data-download-experiment]")) { const item = data.experiments.find(experiment => String(experiment.id) === String(target.dataset.downloadExperiment)); if (item) download(`${item.id || "speech-ai-experiment"}.md`, markdownExperiment(item)); return; }
  });
  document.addEventListener("change", event => { if (event.target.id === "action-category") { board.category = event.target.value; board.showAll = false; renderAll(); } if (event.target.id === "timeline-topic") { timeline.topic = event.target.value; renderAll(); } if (event.target.id === "timeline-project") { timeline.project = event.target.value; renderAll(); } });
  document.addEventListener("input", event => { if (event.target.id === "timeline-search") { timeline.query = event.target.value; renderAll(); const input = $("#timeline-search"); if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); } } });

  try { const parsed = JSON.parse(dataNode?.textContent || "{}"); if (parsed && typeof parsed === "object" && parsed.schema_version) data = { ...fallback, ...parsed }; } catch (_) { data = fallback; }
  data.site = { ...fallback.site, ...(data.site || {}), schedule: { ...fallback.site.schedule, ...((data.site || {}).schedule || {}) } };
  ["projects", "profiles", "events", "actions", "runs", "headlines", "snapshots", "experiments"].forEach(key => { if (!Array.isArray(data[key])) data[key] = []; });
  initTheme(); setSiteMeta(); renderAll(); setInterval(setSiteMeta, 60000);
  const downloadButton = $("#download-digest"); if (downloadButton) downloadButton.addEventListener("click", () => download("speech-ai-research-digest.md", markdownDigest()));
})();
