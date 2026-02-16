#!/usr/bin/env bash
set -euo pipefail

PROJECT_REPO="${HOME_PROJECT_REPO:-capston2025/capston}"
TAP_REPO="${HOME_TAP_REPO:-capston2025/homebrew-gaia}"
TAP_PATH="${HOME_TAP_PATH:-/tmp/homebrew-gaia}"
FORMULA_PATH="Formula/gaia.rb"
TARBALL_URL="https://github.com/${PROJECT_REPO}/archive/refs/heads/main.tar.gz"

if [ -z "${HOMEBREW_TAP_TOKEN:-}" ]; then
  echo "Missing HOMEBREW_TAP_TOKEN secret."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required."
  exit 1
fi

SHA_SUM=$(curl -fsSL "$TARBALL_URL" | shasum -a 256 | awk '{print $1}')
if [ -z "$SHA_SUM" ]; then
  echo "Failed to compute sha256 from $TARBALL_URL"
  exit 1
fi

if [ "${#SHA_SUM}" -ne 64 ]; then
  echo "Invalid sha256 length: $SHA_SUM"
  exit 1
fi

PROJECT_VERSION=$(python3 - <<'PY'
import pathlib, tomllib

cfg = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
print(cfg.get("project", {}).get("version", "0.1.0"))
PY
)

rm -rf "$TAP_PATH"
git clone "https://x-access-token:${HOMEBREW_TAP_TOKEN}@github.com/${TAP_REPO}.git" "$TAP_PATH"

cat > /tmp/update_formula.py <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
version = sys.argv[2]
sha = sys.argv[3]

text = path.read_text(encoding="utf-8")

def replace(pattern: str, value: str) -> None:
    global text
    new = re.sub(pattern, value, text, count=1)
    if new == text:
        raise RuntimeError(f"pattern not found: {pattern}")
    text = new

replace(r'^\\s*version\\s+"[^"]+"$', f'  version "{version}"', text)
replace(r'^\\s*sha256\\s+"[0-9a-f]{{64}}"$', f'  sha256 "{sha}"', text)

path.write_text(text, encoding="utf-8")
PY

python3 /tmp/update_formula.py \
  "$TAP_PATH/$FORMULA_PATH" \
  "$PROJECT_VERSION" \
  "$SHA_SUM"

cd "$TAP_PATH"
if git diff --quiet "$FORMULA_PATH"; then
  echo "No formula change. Already up to date."
  exit 0
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add "$FORMULA_PATH"
git commit -m "chore: bump formula sha for main branch"
git push origin HEAD:main
