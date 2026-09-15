# Third-party components

This package is an original wrapper. It bundles **no** third-party code.
Everything below is fetched from, or installed by, its own copyright holder on
the user's machine.

## Official WPS kdocs skill — `kdocs-app/kdocs-skill`

- Repository: https://github.com/kdocs-app/kdocs-skill
- Vendor: 珠海金山办公软件有限公司 (Kingsoft Office / WPS)
- **License: not declared.** The repository contains no `LICENSE` file and the
  GitHub API reports `license: null` (checked 2026-09-15). The same is true of
  the sibling repo `WPS-SmartDocs/WPS-AirPage-Skill`, so this appears to be the
  vendor's standing practice rather than an oversight.

**Consequence, and what we do about it:** with no license grant, those files
cannot be copied, bundled, or redistributed by us. This package therefore ships
none of them. `requirements.json` marks the skill `redistributable: false` and
`auto: false`, and a test (`tests/test_requirements.py::TestLicenseInvariant`)
fails the build if any copy of the official `references/` ever appears in this
tree.

Install it yourself, from the vendor:

- In-product: https://www.kdocs.cn/latest → avatar menu → 金山文档Skill
- Or fetch https://github.com/kdocs-app/kdocs-skill into `.claude/skills/kdocs/`

It is **optional**. Every command this package wraps works without it; it only
supplies parameter documentation for the ~200 actions reached through `raw`.

## `kdocs-cli` binary

- Vendor: same as above
- Distribution: official CDN, `https://wpsai.wpscdn.cn/skillhub/pro/v<version>/releases/`
- Integrity: the vendor publishes `checksums.txt` (SHA-256) alongside each release

We do not host, mirror, or redistribute this binary. `kdocs_kit.py setup`
downloads the vendor's **own** installer script
(`kdocs-app/kdocs-skill:scripts/setup.sh` / `setup.ps1`), pins the version via
`KDOCS_CLI_VERSION`, and runs it. That script resolves the platform, downloads
from the CDN above, and verifies the SHA-256 itself.

We deliberately do not reimplement that download: doing so would mean
maintaining someone else's installer, and would drift from their checksum and
platform-matrix logic.

## Services contacted at runtime

| Host | Purpose |
| --- | --- |
| `api.wps.cn` | API calls and Skill Hub token exchange |
| `www.kdocs.cn` | Document links, browser sign-in |
| `wpsai.wpscdn.cn` | CLI binary downloads and checksums |
| `raw.githubusercontent.com` | Fetching the vendor's installer script during `setup` |

No telemetry is sent anywhere by this package.

## This package

Everything under `skills/hulu-kdocs-kit/` except as noted above is original work,
MIT licensed — see the repository `LICENSE`.
