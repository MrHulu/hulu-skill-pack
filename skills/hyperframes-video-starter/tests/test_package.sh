#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
skill_dir="$repo_dir/skills/hyperframes-video-starter"

test -f "$repo_dir/README.md"
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
! rg -n -i 'video_factory|assets\.lock|video-critic-murch|quality_profile|ai-center' "$repo_dir/skills"
