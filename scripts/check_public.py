#!/usr/bin/env python3
"""Conservative public-content check, NOT a substitute for reviewing git diff."""
import hashlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = {".git", ".local", ".build", "__pycache__", ".venv"}
MAX_UNREVIEWED_BYTES = 5_000_000
# Reviewed complete JSON/labels and provenance on 2026-10-15/16. This exempts
# only the size gate; content patterns are still checked across the entire file.
REVIEWED_LARGE_FILES = {
    "results/pro6000-heavy/gemma-base-167520ctx-prometheus.json":
        "2e90d5a590006f8be01cca8747e5a0a6435e278702d2962ed94c1831626a2be6",
    "results/pro6000-heavy/gemma-tune-262144ctx-prometheus.json":
        "34d6d464ac7b25fbe67d77196c431ee0e193f76ca8526dd3107c78acce528f54",
}
PATTERNS = [r"-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----",
            r"\b(?:ghp_|github_pat_|glpat-)[A-Za-z0-9_\-]{15,}",
            r"\bsk-bf-[A-Za-z0-9_\-]{16,}",
            r"\bhf_[A-Za-z0-9]{20,}",
            r"\beyJ[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}",
            r"\b(?:192\.168\.|10\.\d+\.\d+\.|172\.(?:1[6-9]|2\d|3[01])\.)\d+",
            r"/Users/[^/\s]+/", r"registry\.flant\.com", r"client-key-data\s*:",
            r"(?m)^kind:\s*[\"']?Secret[\"']?\s*$"]


def problems():
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if not path.is_file() or any(p in SKIP for p in rel.parts) or rel.parts[:2] == ("results", "raw"):
            continue
        if path == pathlib.Path(__file__).resolve():
            continue
        if path.stat().st_size > MAX_UNREVIEWED_BYTES:
            reviewed_hash = REVIEWED_LARGE_FILES.get(rel.as_posix())
            if reviewed_hash is None or hashlib.sha256(path.read_bytes()).hexdigest() != reviewed_hash:
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
