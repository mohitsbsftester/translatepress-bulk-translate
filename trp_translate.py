#!/usr/bin/env python3
"""Entry point for the TranslatePress bulk translation CLI."""

from __future__ import annotations

from trp_tool.cli import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
