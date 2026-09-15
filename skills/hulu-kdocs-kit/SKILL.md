---
name: hulu-kdocs-kit
description: >-
  Operate WPS Cloud Docs / Kingsoft Docs (金山文档, kdocs.cn) through the official
  kdocs-cli, with the sharp edges wrapped: cross-machine CLI discovery, every
  parameter passed via a temp file so Chinese text and quotes survive PowerShell,
  per-service timeouts, Base64 handling, plain-language error translation
  (including the "exit 0 but code != 0" fake success), download integrity checks,
  and a hard rule that original files are never overwritten. Use when the user
  wants to find, read, create, edit, share, download, or comment on a cloud
  document or spreadsheet, or to round-trip a local Excel/Word/PPT/PDF through
  the cloud. Triggers on 金山文档, WPS, 云文档, kdocs, 在线表格, 协同, or a
  kdocs.cn link. Do NOT use for local-only Office files that never go to the
  cloud, WPS desktop app problems, Notion / Google Docs / Feishu docs, or generic
  browser automation. Does not work with enterprise WPS accounts.
license: MIT
compatibility: >-
  Requires Python 3.7+ (standard library only, no pip packages), the official
  kdocs-cli binary (installed for you by `setup`), network access to wps.cn and
  kdocs.cn, and a personal WPS account. Enterprise WPS accounts are blocked
  server-side and cannot work. Full machine-readable list in requirements.json.
metadata:
  author: MrHulu
  version: "1.0"
  requires: '{"bins":["kdocs-cli"],"manifest":"requirements.json"}'
allowed-tools: Bash(python ${CLAUDE_SKILL_DIR}/scripts/kdocs_kit.py *) Read Write
---

# hulu-kdocs-kit

A wrapper around the **official** WPS `kdocs-cli`. It does not reimplement the
official actions catalogued in
[`kdocs-app/kdocs-skill`](https://github.com/kdocs-app/kdocs-skill) — it wraps the
seven things that CLI deliberately leaves to the calling agent, and passes
everything else straight through.

## Pre-flight — do this first, every time

Before the first real command in a session, run the health check:

```bash
python ${CLAUDE_SKILL_DIR}/scripts/kdocs_kit.py doctor
```

If anything comes back ❌, install the missing pieces:

```bash
python ${CLAUDE_SKILL_DIR}/scripts/kdocs_kit.py setup
```

> `${CLAUDE_SKILL_DIR}` is substituted by Claude Code, and matches the
> `allowed-tools` rule above so these two run without a permission prompt.
> On agents that do not substitute it, use the relative `scripts/kdocs_kit.py`
> form shown in the rest of this file.

`setup` reads [`requirements.json`](requirements.json) — the single source of
truth for what this skill needs — installs everything that can be installed
automatically, and prints exact instructions for the rest. `doctor` reads the
same file, so the two can never disagree about what is required.

Add `--dry-run` to see what it would do, or `--only <id>` to handle one item.

**Picking the Python command**: try `python -V`, then `py -3 -V`, then
`python3 -V`, and use the first that works. Windows installs from python.org
provide `python` but **not** `python3`. Examples below use `python`.

## What `setup` will and will not do

| Dependency | Handled how |
| --- | --- |
| `kdocs-cli` binary | Installed automatically by fetching and running **WPS's own** installer from `kdocs-app/kdocs-skill`. We do not reimplement the download and we do not redistribute the binary — your machine fetches it from the copyright holder, and their script verifies SHA-256 against the official `checksums.txt`. |
| `skillOverrides` config | Merged into your `.claude/settings.json` (existing keys preserved, backup written first). Only applies if the official skill is installed. |
| Official `kdocs` skill | **You install it yourself.** Its repo declares no license (no LICENSE file; GitHub reports `license: null`), so its files are not redistributable and are not bundled here. Get it from https://www.kdocs.cn/latest or https://github.com/kdocs-app/kdocs-skill. It is optional — see "Going beyond the wrapped commands". |
| Python, network, WPS account | Reported, never touched. A skill should not install language runtimes, change proxy policy, or log in as you. |

## Signing in

```bash
python scripts/kdocs_kit.py login
```

Opens a browser. Use a **personal** WPS account — enterprise accounts are
rejected by the server with HTTP 403 on every call, and nothing on the client
can work around that.

If you already have a token, `python scripts/kdocs_kit.py set-token` reads it
from the clipboard when called with no argument, so it never appears in shell
history or the conversation.

## Everyday commands

```bash
python scripts/kdocs_kit.py find "quarterly"            # search your drive
python scripts/kdocs_kit.py read <id-or-link>           # read a document
python scripts/kdocs_kit.py new report.xlsx             # create a cloud doc
python scripts/kdocs_kit.py new notes.otl --from-file body.md
python scripts/kdocs_kit.py upload ./local.xlsx         # local -> cloud
python scripts/kdocs_kit.py download <id> ./folder/     # cloud -> local
python scripts/kdocs_kit.py share <id> --scope users    # get a shareable link
python scripts/kdocs_kit.py comments <id>               # read colleagues' comments
```

`--scope` on `share` is **required and has no default**: `anyone` means anyone
holding the link can open it. Defaulting that would quietly turn a private
spreadsheet into a public one, so the caller must say it out loud.

`download` never overwrites: it always writes a new `<name>-copy.<ext>`.

## Going beyond the wrapped commands

Everything else — charts, pivot tables, conditional formatting, replying to
comments, AI PPT, PDF splitting — goes through `raw`, which passes your
parameters to the official CLI unchanged:

```bash
python scripts/kdocs_kit.py raw sheet <action> --params-file params.json
```

Always prefer `--params-file` (or `-` for stdin) over inline JSON: PowerShell
eats quotes when passing arguments to external programs, which silently
corrupts Chinese text and nested JSON.

Parameter documentation for the ~200 actions lives in the **official** skill,
not here. With it installed as a sibling, the routing table is:

| Task | Official reference | Command |
| --- | --- | --- |
| Spreadsheet data / formatting / charts / pivots | `../kdocs/references/sheet.md` | `raw sheet <action>` |
| Text documents | `../kdocs/references/wps.md` | `raw wps <action>` |
| Presentations | `../kdocs/references/wpp.md` | `raw wpp <action>` |
| Smart docs, block-level edits | `../kdocs/references/otl.md` | `raw otl <action>` |
| Multi-dimensional tables | `../kdocs/references/dbsheet.md` | `raw dbsheet <action>` |
| PDF split / convert / translate | `../kdocs/references/pdf.md` | `raw pdf <action>` |
| Permissions, tags, versions, trash | `../kdocs/references/drive.md` | `raw drive <action>` |

Without the official skill, `raw` still works — you just supply the parameters
yourself, from `kdocs-cli <service> <action> --help`.

## Rules this wrapper enforces

1. **Never overwrite an original.** Round-trips always produce a new file.
2. **Ask before uploading.** Local files must go to the user's own cloud drive
   before charts and formatting can be applied. Say so and get agreement first;
   if the answer is no, fall back to plain read/write and say what is lost.
3. **Ask who may see it** before `share`. Name the scope in plain words.
4. **Never claim success you did not verify.** After a write, read it back.
   The CLI can exit 0 with `code != 0` in the JSON body — that is a failure.
5. **Downloads are verified.** WPS download URLs require a logged-in session,
   so a bare fetch gets an HTML login page. If the content does not match its
   expected hash, the file is not written; the user gets the link instead. This
   is normal, not a failure — say "open this link and it will save" rather than
   "the download failed".

## Known limits

- `.xlsm` (macro-enabled Excel) is rejected by WPS upload.
- Saving a cloud file back to disk almost always requires the user to click the
  link in a browser, because the download URL is gated on a login session.
  Upload and cloud-side processing are unaffected.
- Excel processed in the cloud can come back with minor formatting differences.
- Runtime error messages are in Chinese, matching the CLI they translate. The
  install and diagnostic surface (`doctor` / `setup`) is in English.

## Tests

```bash
python -m unittest discover -s tests
```

210 tests, all green (a few skip when offline or when no CLI is installed).
They cover the wrapper's own logic, a contract test that reads the real
`kdocs-cli --help` to confirm required parameters, and drift guards that keep
this file honest — including the test count above.

Real-account verification is a separate manual list: `tests/live_smoke.md`,
12 manual checks against a live account.
