#!/usr/bin/env bash
# Build + publish the static dashboard demo to GitHub Pages (the gh-pages branch).
# Snapshots the live API to JSON, bundles it with the UI, force-pushes gh-pages, ensures Pages is on.
#
#   .venv/bin/uvicorn api.main:app --port 8000 &   # the live read-only API must be running
#   bash scripts/build_demo.sh
#
# Live at: https://<user>.github.io/<repo>/   (default: agambear25/situational-picture)
set -e
REPO="${DEMO_REPO:-agambear25/situational-picture}"
BUILD="${1:-$(mktemp -d)/site}"
VPY="${VPY:-.venv/bin/python}"

echo "[1/3] snapshot the board → $BUILD/data ..."
"$VPY" scripts/export_static.py --out "$BUILD"

echo "[2/3] bundle the UI ..."
cp web/index.html web/app.js web/styles.css "$BUILD/"
touch "$BUILD/.nojekyll"

echo "[3/3] publish to gh-pages ..."
( cd "$BUILD"
  git init -q
  git config http.postBuffer 524288000   # the snapshot is many small files → big pack; lift the 1MB cap
  git add -A
  git -c user.email=demo@local -c user.name=demo commit -qm "Static dashboard demo — board snapshot"
  git push -f "https://github.com/${REPO}.git" HEAD:gh-pages )
gh api -X POST "repos/${REPO}/pages" -f "source[branch]=gh-pages" -f "source[path]=/" >/dev/null 2>&1 || true

echo "Done → https://$(echo "$REPO" | cut -d/ -f1).github.io/$(echo "$REPO" | cut -d/ -f2)/"
