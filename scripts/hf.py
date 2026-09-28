#!/usr/bin/env python3
"""Entry point; rendering, cluster access and lab utilities are separate modules."""
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from workshop.manifests import ROOT, OWNER, STAGES, read_json, render, validate_site
from workshop.cluster import Cluster
from workshop.cli import main

if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        sys.exit(1)
