# Speech AI Research Digest

A single-page, Arabic-first research digest for engineers building their own speech models and datasets—not just integrating hosted APIs.

**Live site:** https://ahmed-ezzat20.github.io/speech-ai-research-digest/

## Run locally

Open `index.html` in a modern browser. No dependencies, framework, build step, or server are required. CSS, JavaScript, icons, and source-profile data are embedded in the HTML. Google Fonts are optional; system-font fallbacks work without internet.

## In this edition

The page includes selected research/open-source milestones, a three-theme edition brief, source-linked “Before you train” checks, expandable research updates, a “Watch next” note, and a proposed Egyptian code-switching ASR experiment. Six source profiles cover Habibi, F5-TTS, Casablanca, Qwen3-TTS, Whisper, and OpenAI realtime speech.

Topic filters, search, local bookmarks, light/dark mode, and Markdown downloads run entirely in the browser. There are no accounts, audio uploads, or backend services. Browser-storage restrictions may prevent preferences from persisting.

This initial edition was curated on **25 October 2026** and includes historical sources. It is **not a live feed or a complete release log**. Suggested experiments are hypotheses, not reported benchmark results. Follow the cited source cards for exact checkpoint, dataset, and code licensing; this repository does not relicense upstream assets.

## Update the content

Edit the visible HTML and its `research-data` JSON block together so that source profiles and Markdown exports remain consistent. Keep factual claims linked to primary sources, and maintain clear distinctions between released code, weights, datasets, and proprietary capabilities.

## GitHub Pages

The site publishes from the root of the `main` branch. `.nojekyll` tells GitHub Pages to serve it as a plain static site. Changes pushed to `main` are deployed by GitHub Pages; no custom workflow is needed.

Inspired by [Ibrahim Sobh’s Coding Agents Digest](https://ibrahimsobh.github.io/coding-agents-digest/).
