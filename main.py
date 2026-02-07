"""CLI entrypoint for mtf-trend backtesting project."""

from __future__ import annotations

from cli.parser import build_parser, resolve_handler
from config import load_config


def main() -> int:
    config = load_config()
    parser = build_parser()
    args = parser.parse_args()
    handler = resolve_handler(args.command)
    return handler(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
