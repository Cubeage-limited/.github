# Cubeage GitHub Actions Infrastructure

Org-wide GitHub Actions configuration and shared reusable workflows.

## macOS CI Runner

iOS builds run on macOS runners provided by Sylphx Runners, not on
GitHub-hosted macOS runners. The reusable workflow asks for the labels
`[self-hosted, sylphx, macos, standard]`. Xcode version and capacity are set by
Sylphx Runners.

## Other shared files

- `.github/workflows/firebase-app-distribution.yml`: reusable workflow that
  uploads an Android APK/AAB to Firebase App Distribution.
- `workflow-templates/ci-fast-path.yml`: the starter for a repository's CI -
  docs-only pull requests skip heavy jobs, superseded runs cancel, heavy jobs
  wait for the cheap gate, `ci-ok` is the required check. It calls the
  reusable `changes.yml`, `cache-toolchain` and `needs-pass` in
  `SylphxAI/.github`; the recipe is its `docs/ci-template.md`.
- `.github/workflows/content-checks.yml`: this repository's own content checks.
  Every Cubeage repository carries its own copy calling the shared
  `SylphxAI/.github` actions: `plain-language` (advisory, never blocks) and
  `zh-hant` (advisory until 2026-10-02, then a required check).
- `.github/actions/game-standard/`: the shared check that keeps a title's
  `game-standard.toml` in step with the Cubeage Game Standard: the module MUST
  rules it answers, its declared first-download and cold-start budgets, and its
  live-ops calendar. It carries rule ids, levels and module names only, and
  title repositories call it from a copy of
  `workflow-templates/game-standard.yml` (a private repository runs it on
  `sylphx-linux-standard`). Every pull request runs mode `pr`; a release
  workflow passes `mode: release` and the built artifact, which must then fit
  the declared budget. Its tests run in
  `.github/workflows/game-standard-action.yml`.
- `.github/actions/ftl-daily-budget/`: the guard a title's Test Lab step runs
  immediately before `gcloud firebase test`. It counts the day's Test Lab steps
  in the project (midnight to midnight, America/Los_Angeles) from the Tool
  Results API and fails the job when the run would pass the daily allowance -
  physical 3, virtual 10 - with the message that spending needs owner approval.
  It fails closed whenever it cannot prove the count. Its tests run in
  `.github/workflows/ftl-daily-budget-action.yml`.
- `scripts/pages-publish.sh`: the one Cloudflare Pages upload every Keel
  title's `scripts/deploy_pages.sh` calls, fetched at a pinned commit from
  `https://raw.githubusercontent.com/Cubeage/.github/<sha>/scripts/pages-publish.sh`.
  It takes only `CLOUDFLARE_API_TOKEN` (Pages Edit): the repository secret in
  CI, `op://Sylphx/wdzsmplds7qlnrjvjhfdkjqqxu/credential` on the desk. The
  Global API Key is owner-only and the script clears it before uploading.
  Usage: `pages-publish.sh <dir> <project> [branch] [-- <wrangler flags>]`;
  workflows fetch it the same way and call it in place of `wrangler pages deploy`.
- Repositories with several workflows also carry a `ci-ok` workflow: the one
  required check of the merge queue, which waits for every other check.

---

## iOS Builds — Quick Start

### Step 1: Add GitHub Secrets to your repo

Go to your repo → **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|--------|-------|
| `BUILD_CERTIFICATE_BASE64` | Your Apple Distribution `.p12` cert, base64 encoded |
| `P12_PASSWORD` | Password you set when exporting the `.p12` |
| `PROVISIONING_PROFILE_BASE64` | Your App Store `.mobileprovision`, base64 encoded |
| `KEYCHAIN_PASSWORD` | Any random strong string (e.g. `openssl rand -base64 32`) |

**How to encode your files:**
```bash
# On your Mac:
base64 -i Distribution.p12 | pbcopy          # → paste as BUILD_CERTIFICATE_BASE64
base64 -i AppStore.mobileprovision | pbcopy   # → paste as PROVISIONING_PROFILE_BASE64
```

**How to export your .p12:**
1. Open Keychain Access
2. Find your "Apple Distribution: ..." certificate
3. Right-click → Export → save as `.p12` with a password
4. Encode it: `base64 -i YourCert.p12 | pbcopy`

### Step 2: Create workflow file

Copy `.github/workflows/` → create `.github/workflows/ios-build.yml` in your repo:

```yaml
name: iOS Build

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build:
    uses: Cubeage/.github/.github/workflows/ios-sign-and-build.yml@main
    with:
      scheme: YOUR_SCHEME_NAME    # replace with your Xcode scheme
      export-method: app-store    # app-store | ad-hoc | development
    secrets:
      BUILD_CERTIFICATE_BASE64: ${{ secrets.BUILD_CERTIFICATE_BASE64 }}
      P12_PASSWORD: ${{ secrets.P12_PASSWORD }}
      PROVISIONING_PROFILE_BASE64: ${{ secrets.PROVISIONING_PROFILE_BASE64 }}
      KEYCHAIN_PASSWORD: ${{ secrets.KEYCHAIN_PASSWORD }}
```

That's it. The reusable workflow handles everything else.

---

## Security Model

Each build gets a **completely isolated temporary keychain** that is:
- Created fresh for every build run (name includes `run_id + run_attempt`)
- Deleted immediately after build completes (even on failure)
- Never shared between concurrent builds or different orgs

This is the same pattern used by GitHub's hosted runners and Fastlane match.

**What runs on the runner:**
- Xcode + system tools (persistent)
- Your source code (via `actions/checkout`, deleted after job)
- Your signing cert (temp keychain only, deleted after job)
- Your provisioning profile (UUID-named file, deleted after job)

**What never persists on the runner:**
- Your Apple ID credentials
- Your signing certificates
- Your provisioning profiles

---

## Simulator Builds (no signing required)

For unit tests or simulator builds, no secrets needed:

```yaml
- name: Test
  run: |
    xcodebuild test \
      -scheme MyApp \
      -destination 'platform=iOS Simulator,name=iPhone 16' \
      -configuration Debug
```

---

## Reusable Workflow Parameters

| Input | Default | Description |
|-------|---------|-------------|
| `scheme` | (required) | Xcode scheme name |
| `configuration` | `Release` | `Debug` or `Release` |
| `export-method` | `app-store` | `app-store`, `ad-hoc`, `development`, `enterprise` |
| `xcode-path` | `/Applications/Xcode.app` | Path to Xcode.app |

---

## Multiple Signing Identities

Each repo manages its own secrets independently. Different repos in different orgs can have different Apple Developer accounts — there's no shared state on the runner.

---

## Runner Maintenance

Runners are managed by Sylphx Runners. A runner defect or an Xcode update
request goes to a `SylphxAI/cloud` issue labelled `platform-request`.
