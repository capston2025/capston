"""Console entry point for GAIA."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence


def _add_common_start_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--url", help="Target URL")
    parser.add_argument("--spec", help="Path to PDF spec")
    parser.add_argument("--plan", help="Path to saved plan JSON")
    parser.add_argument("--resume", help="Resume from terminal run context ID or file path")


def _build_start_parser() -> argparse.ArgumentParser:
    """Build parser for `gaia start ...`."""
    parser = argparse.ArgumentParser(
        prog="gaia start",
        description="Run GAIA in terminal or GUI mode.",
    )

    _add_common_start_options(parser)

    subparsers = parser.add_subparsers(dest="subcommand", required=False)
    gui_parser = subparsers.add_parser(
        "gui",
        prog="gaia start gui",
        help="Start GAIA GUI",
    )
    _add_common_start_options(gui_parser)

    terminal_parser = subparsers.add_parser(
        "terminal",
        prog="gaia start terminal",
        help="Run GAIA terminal mode",
    )
    _add_common_start_options(terminal_parser)

    subparsers.add_parser(
        "help",
        prog="gaia start help",
        help="Show `gaia start` help",
    )

    return parser


def _build_gui_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gaia start gui")
    parser.add_argument("--resume", help="Resume GUI state from terminal run context")
    parser.add_argument("--url", help="Prefill target URL")
    parser.add_argument("--plan", help="Prefill plan JSON path")
    parser.add_argument("--spec", help="Reserved for future use")
    return parser


def _build_terminal_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gaia start terminal")
    parser.add_argument("--plan", type=Path, help="Path to saved plan JSON")
    parser.add_argument("--spec", type=Path, help="Path to PDF spec")
    parser.add_argument("--url", help="Target URL")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path, help="Optional JSON output file")
    parser.add_argument("--resume", help="Resume from terminal run context ID or file path")
    return parser


def _parse_args_or_code(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None,
) -> tuple[argparse.Namespace | None, int | None]:
    try:
        return parser.parse_args(list(argv or [])), None
    except SystemExit as exc:
        return None, int(exc.code if isinstance(exc.code, int) else 0)


def _prompt_mode() -> str:
    print("GAIA 실행 모드 선택")
    print("1) 터미널 모드 (start terminal)")
    print("2) GUI 모드 (start gui)")
    while True:
        answer = input("선택 [1/2]: ").strip().lower()
        if answer in {"1", "terminal"}:
            return "terminal"
        if answer in {"2", "gui"}:
            return "gui"
        print("1 또는 2를 입력해주세요.")


def _collect_gui_args_from_start(parsed: argparse.Namespace) -> list[str]:
    forwarded: list[str] = []
    if parsed.resume:
        forwarded += ["--resume", str(parsed.resume)]
    if parsed.url:
        forwarded += ["--url", str(parsed.url)]
    if parsed.plan:
        forwarded += ["--plan", str(parsed.plan)]
    if parsed.spec:
        forwarded += ["--spec", str(parsed.spec)]
    return forwarded


def _collect_terminal_args_from_start(parsed: argparse.Namespace) -> list[str]:
    forwarded: list[str] = []
    if parsed.plan:
        forwarded += ["--plan", str(parsed.plan)]
    if parsed.spec:
        forwarded += ["--spec", str(parsed.spec)]
    if parsed.url:
        forwarded += ["--url", str(parsed.url)]
    if parsed.format:
        forwarded += ["--format", str(parsed.format)]
    if parsed.output:
        forwarded += ["--output", str(parsed.output)]
    if parsed.resume:
        forwarded += ["--resume", str(parsed.resume)]
    return forwarded


def run_start(argv: Sequence[str] | None = None) -> int:
    args, exit_code = _parse_args_or_code(_build_start_parser(), argv)
    if exit_code is not None:
        return exit_code

    if args.subcommand == "gui":
        return run_gui(_collect_gui_args_from_start(args))

    if args.subcommand == "terminal":
        return run_terminal(_collect_terminal_args_from_start(args))

    if args.subcommand == "help":
        _build_start_parser().print_help()
        return 0

    mode = _prompt_mode()
    if mode == "gui":
        return run_gui(_collect_gui_args_from_start(args))
    return run_terminal(_collect_terminal_args_from_start(args))


def run_gui(argv: Sequence[str] | None = None) -> int:
    args, exit_code = _parse_args_or_code(_build_gui_parser(), argv)
    if exit_code is not None:
        return exit_code

    from gaia.main import main as launch_gui

    forwarded: list[str] = []
    if args.resume:
        forwarded.extend(["--resume", str(args.resume)])
    if args.url:
        forwarded.extend(["--url", str(args.url)])
    if args.plan:
        forwarded.extend(["--plan", str(args.plan)])
    if args.spec:
        forwarded.extend(["--spec", str(args.spec)])

    return launch_gui(forwarded)


def run_terminal(argv: Sequence[str] | None = None) -> int:
    from gaia.terminal import run_terminal as terminal_entry

    args, exit_code = _parse_args_or_code(_build_terminal_parser(), argv)
    if exit_code is not None:
        return exit_code

    forwarded: list[str] = []
    if args.plan:
        forwarded.extend(["--plan", str(args.plan)])
    if args.spec:
        forwarded.extend(["--spec", str(args.spec)])
    if args.url:
        forwarded.extend(["--url", str(args.url)])
    if args.format:
        forwarded.extend(["--format", str(args.format)])
    if args.output:
        forwarded.extend(["--output", str(args.output)])
    if args.resume:
        forwarded.extend(["--resume", str(args.resume)])

    return terminal_entry(forwarded)


def _build_main_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaia",
        description="GAIA command line interface",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("start", help="Choose start mode")
    subparsers.add_parser("gui", help="Start GAIA GUI")
    subparsers.add_parser("terminal", help="Run GAIA terminal mode")
    subparsers.add_parser("help", help="Show help")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return run_start([])

    if args[0] in {"-h", "--help", "help"}:
        _build_main_parser().print_help()
        return 0

    if args[0] == "start":
        return run_start(args[1:])
    if args[0] == "gui":
        return run_gui(args[1:])
    if args[0] == "terminal":
        return run_terminal(args[1:])

    _build_main_parser().print_help()
    print(f"Unknown command: {args[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
