#!/usr/bin/env python3
"""New RQ2 Small Basic runner. Keeps legacy run_smallbasic.py intact."""
from benchmark_v2.cli import main
if __name__ == "__main__":
    raise SystemExit(main("smallbasic"))
