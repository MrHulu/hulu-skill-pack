---
name: hyperframes-video-starter
description: >-
  Creates a generic Hyperframes video project and optionally adds synthetic
  narration with burned-in subtitles. Use when the user wants a simple,
  editable HTML-video draft or a Hyperframes starter, not a directed or
  publish-ready production workflow.
---

# Hyperframes Video Starter

Use this as a normal open-source baseline for a lightweight video draft.
It calls the public Hyperframes CLI directly and adds only basic narration and
hard subtitles.

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

This starter does not provide story planning, content research, branding,
professional sound design, quality gates, review, or delivery workflow.

## Sources

- [Hyperframes](https://github.com/heygen-com/hyperframes) — Apache-2.0,
  pinned to `0.7.64` by the scripts.
- [edge-tts](https://github.com/rany2/edge-tts)
- [FFmpeg subtitle filter](https://ffmpeg.org/ffmpeg-filters.html)
