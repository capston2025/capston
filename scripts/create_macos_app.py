#!/usr/bin/env python3
"""Create a lightweight macOS .app launcher for the local GAIA GUI."""

from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _bundle_identifier(app_name: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in app_name).strip("-")
    return f"dev.gaia.{normalized or 'desktop'}"


def _launcher_script(*, repo_root: Path, python_bin: str | None, gui_args: list[str]) -> str:
    quoted_repo = shlex.quote(str(repo_root))
    quoted_python = shlex.quote(str(python_bin)) if python_bin else ""
    quoted_args = " ".join(shlex.quote(arg) for arg in gui_args)
    explicit_python_block = (
        f'PYTHON_BIN={quoted_python}\n'
        'if [[ ! -x "$PYTHON_BIN" ]]; then\n'
        '  echo "Configured Python is not executable: $PYTHON_BIN" >&2\n'
        "  exit 1\n"
        "fi\n"
        if python_bin
        else (
            'if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then\n'
            '  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"\n'
            "else\n"
            '  PYTHON_BIN="$(command -v python3 || true)"\n'
            '  if [[ -z "$PYTHON_BIN" ]]; then\n'
            '    echo "python3 was not found and .venv/bin/python is missing." >&2\n'
            "    exit 1\n"
            "  fi\n"
            "fi\n"
        )
    )
    args_suffix = f" {quoted_args}" if quoted_args else ""
    return f"""#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT={quoted_repo}
cd "$REPO_ROOT"

LOG_DIR="$HOME/Library/Logs/GAIA"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/gaia-gui.log"

export PYTHONUNBUFFERED=1
export PATH="$REPO_ROOT/.venv/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$REPO_ROOT/.env"
  set +a
fi

{explicit_python_block}
APP_ARGS=()
for arg in "$@"; do
  case "$arg" in
    -psn_*) ;;
    *) APP_ARGS+=("$arg") ;;
  esac
done

if [[ ${{#APP_ARGS[@]}} -gt 0 ]]; then
  exec "$PYTHON_BIN" -m gaia.main{args_suffix} "${{APP_ARGS[@]}}" >>"$LOG_FILE" 2>&1
else
  exec "$PYTHON_BIN" -m gaia.main{args_suffix} >>"$LOG_FILE" 2>&1
fi
"""


def _write_launcher(path: Path, *, repo_root: Path, python_bin: str | None, gui_args: list[str]) -> None:
    path.write_text(
        _launcher_script(repo_root=repo_root, python_bin=python_bin, gui_args=gui_args),
        encoding="utf-8",
    )
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_plist(contents_dir: Path, *, app_name: str, executable_name: str) -> None:
    plist = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": app_name,
        "CFBundleExecutable": executable_name,
        "CFBundleIdentifier": _bundle_identifier(app_name),
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": app_name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
    }
    with (contents_dir / "Info.plist").open("wb") as fp:
        plistlib.dump(plist, fp, sort_keys=True)


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _create_applescript_app(app_path: Path, *, app_name: str) -> bool:
    if shutil.which("osacompile") is None:
        return False
    launcher_path = app_path / "Contents" / "Resources" / "launch-gaia.sh"
    applescript = (
        f"set launcherPath to {_applescript_string(str(launcher_path))}\n"
        'do shell script quoted form of launcherPath & " >/dev/null 2>&1 &"\n'
    )
    subprocess.run(
        ["osacompile", "-o", str(app_path), "-e", applescript],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    info_plist = app_path / "Contents" / "Info.plist"
    if info_plist.exists():
        with info_plist.open("rb") as fp:
            plist = plistlib.load(fp)
        plist.update(
            {
                "CFBundleDisplayName": app_name,
                "CFBundleIdentifier": _bundle_identifier(app_name),
                "CFBundleName": app_name,
                "CFBundleShortVersionString": "0.1.0",
                "CFBundleVersion": "1",
                "NSHighResolutionCapable": True,
            }
        )
        with info_plist.open("wb") as fp:
            plistlib.dump(plist, fp, sort_keys=True)
    return True


def _codesign_app(app_path: Path) -> None:
    if shutil.which("codesign") is None:
        return
    result = subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(app_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        print(f"warning: codesign failed: {message}", file=sys.stderr)


def create_app(
    *,
    repo_root: Path,
    output_dir: Path,
    app_name: str,
    python_bin: str | None,
    gui_args: list[str],
) -> Path:
    app_path = output_dir / f"{app_name}.app"
    if app_path.exists():
        shutil.rmtree(app_path)

    if _create_applescript_app(app_path, app_name=app_name):
        resources_dir = app_path / "Contents" / "Resources"
        resources_dir.mkdir(parents=True, exist_ok=True)
        _write_launcher(
            resources_dir / "launch-gaia.sh",
            repo_root=repo_root,
            python_bin=python_bin,
            gui_args=gui_args,
        )
        _codesign_app(app_path)
        return app_path

    contents_dir = app_path / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    executable_name = app_name.replace("/", "-")
    launcher_path = macos_dir / executable_name
    _write_launcher(launcher_path, repo_root=repo_root, python_bin=python_bin, gui_args=gui_args)
    _write_plist(contents_dir, app_name=app_name, executable_name=executable_name)
    _codesign_app(app_path)

    return app_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local macOS launcher app for GAIA GUI.")
    parser.add_argument("--app-name", default="GAIA")
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--python", dest="python_bin", default="")
    parser.add_argument(
        "--gui-arg",
        action="append",
        default=[],
        help="Argument to pass to 'python -m gaia.main'. Repeat for multiple args.",
    )
    args = parser.parse_args()

    repo_root = _repo_root()
    output_dir = (repo_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    python_bin = str(Path(args.python_bin).expanduser().resolve()) if args.python_bin else None
    app_path = create_app(
        repo_root=repo_root,
        output_dir=output_dir,
        app_name=str(args.app_name or "GAIA"),
        python_bin=python_bin,
        gui_args=[str(item) for item in args.gui_arg],
    )
    print(app_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
