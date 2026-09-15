# hulu-kdocs-kit

**Let your coding agent drive 金山文档 (WPS Cloud Docs) without the sharp edges.**
一句话：给 AI 助手接上金山文档，装依赖这件事它自己搞定。

A wrapper around WPS's **official** `kdocs-cli`. It does not reimplement the
official actions — it wraps the seven things that CLI deliberately leaves to the
calling agent, and passes everything else straight through.

**What it does**

- Find, read, create, share, comment on, and download cloud documents
- Round-trip a local Excel/Word/PPT/PDF through the cloud to apply charts and formatting
- Reach every other official action through `raw` passthrough
- **Install its own dependencies** — one command, on a machine with nothing set up

**What it does not do**

- Work with enterprise WPS accounts (blocked server-side; no client-side fix exists)
- Touch local-only Office files without going through the cloud
- Notion / Google Docs / Feishu docs, or WPS desktop app problems
- Redistribute WPS's official skill or binary (see [THIRD_PARTY.md](THIRD_PARTY.md))

## What it looks like

Real output on a machine with nothing installed — not a mock-up:

```console
$ python .claude/skills/hulu-kdocs-kit/scripts/kdocs_kit.py doctor
✅ Python 3.7+ — Python 3.9.6 (need 3.7+)
❌ kdocs-cli (official WPS binary) — kdocs-cli not installed
   why: Every action is executed by the official CLI. This skill never talks to
        the WPS API directly -- it only wraps the CLI.
➖ Official `kdocs` skill (reference docs for raw passthrough) — not installed
✅ Network access to WPS — WPS reachable (https://api.wps.cn/)
➖ Claude Code 2.1.129+ — claude not found on PATH (skipped)
➖ skillOverrides entry for the official skill — not applicable
➖ A personal WPS account (not an enterprise one) — no CLI yet, cannot check sign-in

How to fix:
  · kdocs-cli: run  python kdocs_kit.py setup --only kdocs-cli

Missing required items above. Fix them, then run doctor again.
exit=1
```

Then one command fixes it:

```console
$ python .claude/skills/hulu-kdocs-kit/scripts/kdocs_kit.py setup
  🎉 kdocs-cli v2.5.29 ready!
── kdocs-cli (official WPS binary) (required)
  source: https://raw.githubusercontent.com/kdocs-app/kdocs-skill/master/scripts/setup.sh
  env:    {'KDOCS_CLI_VERSION': '2.5.29'}
   ✅ done

Re-checking…
✅ kdocs-cli (official WPS binary) — kdocs-cli v2.5.29 at /tmp/hkk2/u/.local/bin/kdocs-cli
❌ A personal WPS account (not an enterprise one) — not signed in to WPS

Setup finished. Next: python kdocs_kit.py login
exit=2
```

`setup` fetched **WPS's own installer** and ran it. Nothing was vendored, and
the binary's SHA-256 was verified by the vendor's script against their published
[`checksums.txt`](https://wpsai.wpscdn.cn/skillhub/pro/v2.5.29/releases/checksums.txt).

## Install

```bash
git clone https://github.com/MrHulu/hulu-skill-pack.git && \
  cp -R hulu-skill-pack/skills/hulu-kdocs-kit ~/.claude/skills/ && \
  python ~/.claude/skills/hulu-kdocs-kit/scripts/kdocs_kit.py setup
```

Use `.claude/skills/` inside a project instead of `~/.claude/skills/` if you want
it for one repo only. On Windows use `python` or `py -3` — python.org builds do
not provide `python3`.

## First thing to say to your agent

```
用 hulu-kdocs-kit 帮我连上金山文档，然后找一下我云盘里的表格。
```

or in English:

```
Use hulu-kdocs-kit to connect my WPS account, then list the spreadsheets in my drive.
```

The agent will run `doctor`, install anything missing, walk you through the
browser sign-in, and then start working.

## Dependencies

There is no `requirements.txt`, because there are no pip packages — the script is
standard library only. The real dependencies are a native binary, an optional
sibling skill, a platform version and an account type, which pip cannot express.
They live in [`requirements.json`](requirements.json), which is read by **both**
`doctor` (report) and `setup` (install), so the two cannot drift apart. A test
fails the build if the manifest ever names something the code cannot check.

| | |
| --- | --- |
| Python | 3.7+, standard library only |
| `kdocs-cli` | [official binary](https://github.com/kdocs-app/kdocs-skill), installed for you, pinned to 2.5.29 |
| Official `kdocs` skill | optional; only supplies parameter docs for `raw` |
| Account | personal WPS account — enterprise accounts cannot work |

## Safety boundary

**No API keys.** Sign-in is a browser OAuth flow; the token is stored in your
system keychain, never in a file in this repo. `set-token` reads from the
clipboard when given no argument, so a token never lands in shell history or in
your conversation with the agent.

**It stops and asks you** before uploading a local file to the cloud (required
for charts and formatting — say no and it falls back to plain read/write), and
before sharing, because `--scope anyone` means anyone with the link can read it.
`--scope` has no default for exactly that reason.

**It never overwrites your originals.** Round-trips always write a new file.

**It refuses to write a file it cannot verify.** WPS download URLs need a
logged-in session, so a bare fetch returns an HTML login page; without the
integrity check that page would be saved as your `.xlsx`. When verification
fails you get the link to click instead — and it says so, rather than reporting
a success that did not happen.

**Data that leaves your machine:** only what you ask it to send, to WPS's own
hosts. No telemetry. The full list of hosts is in [THIRD_PARTY.md](THIRD_PARTY.md).

## Tests

```bash
cd ~/.claude/skills/hulu-kdocs-kit && python -m unittest discover -s tests
```

210 tests; 3 skip unless you are online, have the CLI installed, or opt into the
live-account run. They cover the wrapper's logic against a stub CLI, a contract
test that reads the **real** `kdocs-cli --help` to confirm required parameters,
and drift guards that keep the docs honest — including the test count in this
sentence.

Real-account verification is a separate manual list: [`tests/live_smoke.md`](tests/live_smoke.md).

## Provenance

The wrapper's seven concerns, the error translations and most of the test suite
come from a private build that was reviewed by four models and then validated on
a real Windows 10 / PowerShell 5.1 machine against a live account. This public
version keeps that work, drops everything specific to the original user, and adds
the dependency manifest and installer.
