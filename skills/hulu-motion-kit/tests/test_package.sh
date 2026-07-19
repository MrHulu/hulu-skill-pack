#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
skill_dir="$repo_dir/skills/hulu-motion-kit"

test -f "$repo_dir/README.md"
test -f "$repo_dir/README.en.md"
rg -F '中文 | [English](README.en.md)' "$repo_dir/README.md"
rg -F '[中文](README.md) | English' "$repo_dir/README.en.md"
test -f "$repo_dir/LICENSE"
test -f "$repo_dir/AGENTS.md"
test -f "$repo_dir/CONTRIBUTING.md"
test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/agents/openai.yaml"
test -x "$skill_dir/scripts/init_hyperframes.sh"
test -x "$skill_dir/scripts/render_with_voice.sh"
rg -F 'hyperframes@0.7.64' "$skill_dir/scripts"
rg -F 'edge-tts' "$skill_dir/scripts/render_with_voice.sh"
rg -F 'subtitles=' "$skill_dir/scripts/render_with_voice.sh"
rg -F 'name: hulu-motion-kit' "$skill_dir/SKILL.md"
public_files=(
  "$repo_dir/README.md"
  "$repo_dir/README.en.md"
  "$repo_dir/AGENTS.md"
  "$repo_dir/CONTRIBUTING.md"
  "$skill_dir/SKILL.md"
  "$skill_dir/THIRD_PARTY.md"
  "$skill_dir/agents/openai.yaml"
  "$skill_dir/scripts"
  "$skill_dir/evals"
)
! rg -n -i 'video_factory|assets\.lock|video-critic-murch|quality_profile|ai-center|hulu lite skills|hulu-lite-skills|hyperframes-video-starter' "${public_files[@]}"
