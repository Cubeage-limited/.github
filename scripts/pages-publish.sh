#!/usr/bin/env bash
# Publish a built directory to a Cloudflare Pages project with a scoped token.
# Shared by every Cubeage title's scripts/deploy_pages.sh; each title keeps its
# own build and identity stamping and calls this for the upload only.
#
#   pages-publish.sh <dir> <pages-project> [branch] [-- <wrangler pages deploy flags>...]
#
# branch defaults to main. Flags after "--" go to `wrangler pages deploy`
# unchanged (for example --commit-hash, --commit-message, --commit-dirty=false);
# without a --commit-dirty flag the upload is marked --commit-dirty=true.
# wrangler runs at the exact version WRANGLER_VERSION through npx, or through
# bunx on runners without Node. Bump it on purpose: a floating major resolved
# 4.146.0 minutes after its publish and 404'd every Pages job until the tarball
# propagated (2026-10-01).
#
# Credential: CLOUDFLARE_API_TOKEN, an account-owned token with Pages Edit.
#   CI:   the repository's CLOUDFLARE_API_TOKEN secret.
#   Desk: op://Sylphx/wdzsmplds7qlnrjvjhfdkjqqxu/credential ("Desk - Pages and Workers").
# The Global API Key (CLOUDFLARE_API_KEY + CLOUDFLARE_EMAIL) is owner-only and
# is never used: it is cleared from the environment before wrangler runs.
#
# PAGES_PUBLISH_ATTEMPTS (default 3) bounds upload retries; a killed or failed
# upload re-sends only the missing content-addressed files.
set -euo pipefail

usage() {
  echo "usage: pages-publish.sh <dir> <pages-project> [branch] [-- <wrangler flags>...]" >&2
  exit 64
}
[ "$#" -ge 2 ] || usage
dir="$1"
project="$2"
shift 2
branch=main
if [ "$#" -gt 0 ] && [ "$1" != "--" ]; then
  branch="$1"
  shift
fi
if [ "$#" -gt 0 ]; then
  [ "$1" = "--" ] || usage
  shift
fi
extra=("$@")
dirty=(--commit-dirty=true)
for flag in "${extra[@]}"; do
  case "$flag" in --commit-dirty*) dirty=() ;; esac
done
WRANGLER_VERSION=4.145.0
if command -v npx >/dev/null 2>&1; then
  wrangler=(npx --yes "wrangler@$WRANGLER_VERSION")
elif command -v bunx >/dev/null 2>&1; then
  wrangler=(bunx "wrangler@$WRANGLER_VERSION")
else
  echo "pages-publish: needs npx (Node) or bunx (Bun) to run wrangler" >&2
  exit 69
fi

test -d "$dir" || { echo "pages-publish: $dir is not a directory" >&2; exit 66; }

unset CLOUDFLARE_API_KEY CLOUDFLARE_EMAIL
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  echo "pages-publish: CLOUDFLARE_API_TOKEN is not set (a Pages Edit token;" >&2
  echo "  desk: op://Sylphx/wdzsmplds7qlnrjvjhfdkjqqxu/credential)" >&2
  exit 2
fi
: "${CLOUDFLARE_ACCOUNT_ID:=b80c8e380d7f83feb86646c854e8d93c}"
export CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID

attempts="${PAGES_PUBLISH_ATTEMPTS:-3}"
for attempt in $(seq 1 "$attempts"); do
  echo "pages-publish: $dir -> $project ($branch), attempt $attempt/$attempts"
  if "${wrangler[@]}" pages deploy "$dir" \
      --project-name="$project" \
      --branch="$branch" \
      "${dirty[@]}" "${extra[@]}"; then
    exit 0
  fi
  [ "$attempt" -lt "$attempts" ] && sleep $((attempt * 15))
done
echo "pages-publish: upload failed after $attempts attempts; nothing new was published" >&2
exit 1
