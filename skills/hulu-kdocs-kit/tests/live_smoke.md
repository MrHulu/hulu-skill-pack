# Live-account smoke checklist

> The automated suite runs against a **stub CLI** — it verifies this wrapper's own
> logic. The real chain (real binary, real API, real account) can only be checked
> by hand, once, on a machine that is installed and signed in.
>
> Run it after `setup` + `login` succeed. Commands below are PowerShell; on
> macOS/Linux swap the backslashes for forward slashes.
>
> Replace `python` with whatever the Pre-flight probe in `SKILL.md` found
> (`python`, or `py -3` on Windows).

Record the **actual** result of every row. "Passed" on its own is not a result.

| # | Command | Expected | Actual |
|---|------|------|------|
| 1 | `python .claude\skills\hulu-kdocs-kit\scripts\kdocs_kit.py doctor` | exit 0; shows `signed in to WPS` | |
| 2 | `$env:KDOCS_E2E_LIVE="1"; cd .claude\skills\hulu-kdocs-kit; python -m unittest tests.test_e2e.TestLiveSmoke -v` | OK, no longer skipped | |
| 3 | `python ...\kdocs_kit.py find 表` | lists documents that really exist in your drive, **each with a name and an ID** — never the placeholder「没有名字」 | |
| 4 | `python ...\kdocs_kit.py new smoketest.otl` | returns a file_id + link; the file appears on the website; output confirms it was read back | |
| 5 | `python ...\kdocs_kit.py share <id from #4> --scope anyone` | a kdocs.cn link that opens in a browser; output spells out that anyone holding the link can open it | |
| 6 | `python ...\kdocs_kit.py comments <id from #4>` | empty comment list, no error | |
| 7 | Make a 3-row .xlsx, then `python ...\kdocs_kit.py upload <path>` | the file appears in your drive with matching content | |
| 8 | `python ...\kdocs_kit.py raw sheet get-sheets-info --params-file p.json` (p.json = `{"file_id":"..."}` from #7) | returns worksheet info | |
| 9 | `python ...\kdocs_kit.py raw sheet add-chart --params-file p.json` (parameters per `../kdocs/references/sheet.md`) | the chart is visible on the website | |
| 10 | `python ...\kdocs_kit.py download <id from #7> <an existing folder>` | either the file really lands on disk with the original untouched, **or** it says clearly that you need to 在浏览器里点一下 and gives the link — both are correct. **What is never acceptable is silently writing a broken file.** | |
| 11 | Run `doctor` once while signed in with an **enterprise** account, if you have one | says you need a personal account — **not**「请求太频繁」(rate limited) | |
| 12 | Clean up: delete the files from #4 and #7 | both are in the trash | |

**If any row disagrees with the expectation, do not adjust the test to match.
Write down what actually happened.**

## Why rows 3, 10 and 11 get their own attention

These three are the acceptance points for bugs that a four-model review found in
this wrapper — each one shipped green unit tests while being wrong against the
real API:

- **Row 3** — `render_files` was reading a response shape that does not exist.
  Against the real API every result would have rendered as「没有名字」.
- **Row 10** — downloads were fetched with no session, so the URL returns an HTML
  login page. Without the integrity check that page gets written out as a
  `.xlsx`, and the user opens a corrupt file. The wrapper must refuse to write it.
- **Row 11** — an enterprise account returns 403. An early version mapped that to
  the rate-limit message, which tells the user to wait — for something that will
  never succeed no matter how long they wait.

## Rows 1–2 are the install acceptance

They are what proves `setup` produced a working environment rather than just
printing success. Row 1 exercising `doctor` on a signed-in machine is the whole
first-run promise of this package.
