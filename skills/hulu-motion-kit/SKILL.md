---
name: hulu-motion-kit
description: >-
  Creates an editable video project through the Hulu Motion Kit workflow, with
  optional synthetic narration and burned-in subtitles. Use when the user wants
  a practical Hyperframes-based video foundation, not a fully directed or
  publish-ready production workflow.
---

# Hulu Motion Kit

Hulu Motion Kit is an original public workflow package for a practical,
editable video foundation. It uses the public Hyperframes CLI for HTML-video
rendering and adds a basic narration/subtitle handoff.

## Start a project

```bash
bash scripts/init_hyperframes.sh my-video
```

Edit the upstream-generated project according to its README, then use
Hyperframes' own render workflow for visual iteration.

## Render with a basic voice and subtitles

Create a UTF-8 `words.txt`, then run:

```bash
bash scripts/render_with_voice.sh ./my-video words.txt rough.mp4
```

Requirements: Node.js with `npx`, FFmpeg, and `edge-tts`
(`pip install edge-tts`).

## Boundary

This package does not provide story planning, content research, branding,
professional sound design, quality gates, review, or delivery workflow.

## Sources

- [Hyperframes](https://github.com/heygen-com/hyperframes) — Apache-2.0,
  pinned to `0.7.64` by the scripts.
- [edge-tts](https://github.com/rany2/edge-tts)
- [FFmpeg subtitle filter](https://ffmpeg.org/ffmpeg-filters.html)
