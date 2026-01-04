from __future__ import annotations

import argparse
import sys
from typing import Sequence


def _cmd_delegate(module_main: str, argv: list[str]) -> int:
    import runpy

    old_argv = sys.argv
    try:
        sys.argv = [module_main] + argv
        try:
            runpy.run_module(module_main, run_name="__main__")
            return 0
        except SystemExit as e:
            code = e.code
            if code is None:
                return 0
            if isinstance(code, int):
                return int(code)
            return 1
    finally:
        sys.argv = old_argv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m geodesy",
        description="Geodesy command hub (roadways, spirals, highpoints, ...).",
    )
    p.add_argument("cmd", nargs="?", help="Subcommand: roadways | spirals | highpoints")
    p.add_argument("args", nargs=argparse.REMAINDER, help="Args passed through to subcommand.")

    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        build_parser().print_help()
        return 0

    cmd, rest = args[0], args[1:]
    if cmd == "roadways":
        return _cmd_delegate("roadways", rest)
    if cmd == "spirals":
        return _cmd_delegate("spirals", rest)
    if cmd == "highpoints":
        return _cmd_delegate("highpoints", rest)

    print(f"error: unknown command {cmd!r}", file=sys.stderr)
    build_parser().print_help()
    return 2
