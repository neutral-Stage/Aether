#!/usr/bin/env python3
"""Convenience launcher so you can run `python run.py ...` from the repo root."""
import sys

from aether.app import main

if __name__ == "__main__":
    sys.exit(main())
