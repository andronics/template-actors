#!/bin/sh
# Template-owned: template-actors. The actor manifests' checks, run by scripts/checks.sh:
# each .github/actors/*.yml against actor.schema.json, the rules a schema can't say (app_*
# fields only on github-app actors, one App per role, shared Apps agree, no secret values), and
# .github/actors/README.md up to date. Locally it also confirms each live key's pass entry
# exists, never decrypting it; CI has no password store. Avatars kept here meet the spec.
# Quiet on a pass: one line.
set -eu
cd "$(dirname "$0")/../.."

command -v uv > /dev/null || { echo "error: 40-actors needs uv (https://docs.astral.sh/uv/)"; exit 1; }
if [ -n "${CI:-}" ]; then
    uv run --quiet scripts/actors.py check
else
    uv run --quiet scripts/actors.py check --local
fi
set -- .github/actors/*.png
if [ -e "$1" ]; then
    out=$(uv run --quiet scripts/actors-avatar.py --check "$@") || { echo "$out"; exit 1; }
fi
