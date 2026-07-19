#!/usr/bin/env bash
set -euo pipefail

project_dir=${1:?Usage: render_with_voice.sh <hyperframes-project> <words.txt> <output.mp4> [voice]}
words_file=${2:?Usage: render_with_voice.sh <hyperframes-project> <words.txt> <output.mp4> [voice]}
output_file=${3:?Usage: render_with_voice.sh <hyperframes-project> <words.txt> <output.mp4> [voice]}
voice=${4:-zh-CN-XiaoxiaoNeural}

command -v npx >/dev/null || { echo "Node.js and npx are required." >&2; exit 1; }
command -v edge-tts >/dev/null || { echo "edge-tts is required." >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "FFmpeg is required." >&2; exit 1; }
test -d "$project_dir" || { echo "Hyperframes project not found." >&2; exit 1; }
test -s "$words_file" || { echo "Words file is empty or missing." >&2; exit 1; }

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT
audio_file="$tmp_dir/voice.mp3"
subtitles_file="$tmp_dir/subtitles.srt"
base_file="$tmp_dir/base.mp4"

edge-tts -f "$words_file" -v "$voice" --write-media "$audio_file" --write-subtitles "$subtitles_file"
(
  cd "$project_dir"
  npx --yes hyperframes@0.7.64 render -o "$base_file"
)
ffmpeg -y -i "$base_file" -i "$audio_file" \
  -vf "subtitles=filename='$subtitles_file':charenc=UTF-8:force_style='FontSize=18,Outline=1,Alignment=2,MarginV=110'" \
  -c:v libx264 -preset medium -crf 25 -c:a aac -b:a 128k -shortest "$output_file"
