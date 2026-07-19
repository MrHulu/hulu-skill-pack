# Hulu Skill Pack

[中文](README.md) | English

A personal collection of original skill packages for AI coding agents. Each
skill is a self-contained public starting point: it preserves the essential
capability and states its boundaries clearly, so it can be read, installed,
and adapted directly.

All skills live under `skills/<skill-name>/`. Read the relevant `SKILL.md`
before use.

## Available skills

| Skill | What it provides |
| --- | --- |
| [Hulu Motion Kit](skills/hulu-motion-kit/) | A Hyperframes-based video-creation package with project initialization, basic narration, and burned-in subtitles. |

## Getting started

```bash
git clone https://github.com/MrHulu/hulu-skill-pack.git
```

Open the target skill's `SKILL.md`, or copy the whole skill directory into your
AI coding agent's skill directory.

## Principles

- Every package is independent, clear, and verifiable.
- Third-party capabilities are acknowledged transparently; this repository
  maintains the packaging and workflow.
- Public packages are reliable starting points. Complex projects still need
  their own creation, review, and delivery process.

## Contributing

Contributions are welcome when they are self-contained, clear, and safe to
publish. See [CONTRIBUTING.md](CONTRIBUTING.md).
