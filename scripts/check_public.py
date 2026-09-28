#!/usr/bin/env python3
"""Conservative public-content check, NOT a substitute for reviewing git diff."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = {".git", ".local", ".build", "__pycache__", ".venv"}
PATTERNS = [r"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----",
            r"\b(?:ghp_|github_pat_|glpat-)[A-Za-z0-9_\-]{15,}",
            r"\b(?:192\.168\.|10\.\d+\.\d+\.|172\.(?:1[6-9]|2\d|3[01])\.)\d+",
            r"/Users/[^/\s]+/", r"registry\.flant\.com", r"client-key-data\s*:"]


def problems():
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if not path.is_file() or any(p in SKIP for p in rel.parts) or rel.parts[:2] == ("results", "raw"):
            continue
        if path == pathlib.Path(__file__).resolve():
            continue
        if path.stat().st_size > 5_000_000:
            yield f"{rel}: large file requires manual review"
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue  # Images must still be visually redacted before publication.
        for pattern in PATTERNS:
            if re.search(pattern, text):
                yield f"{rel}: potential private content ({pattern})"
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#") or " " in target:
                    continue
                file = target.split("#", 1)[0]
                if file and not (path.parent / file).exists():
                    yield f"{rel}: missing local link {file}"


if __name__ == "__main__":
    failures = list(problems())
    print("\n".join(failures) if failures else "Public-content and local-link checks passed.")
    sys.exit(bool(failures))
