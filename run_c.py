#!/usr/bin/env python3
"""New RQ2 C runner. Keeps legacy run_c.py intact for parity testing."""
from benchmark_v2.cli import main
if __name__ == "__main__":
    raise SystemExit(main("c"))
