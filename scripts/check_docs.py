#!/usr/bin/env python3
"""Parse fenced shell blocks and hf CLI arguments without executing the commands."""
import pathlib
import re
import shlex
import subprocess
import sys
from workshop.cli import make_parser

ROOT = pathlib.Path(__file__).resolve().parents[1]


def check():
    errors = []
    count = 0
    for path in ROOT.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(ROOT).parts):
            continue
        for block in re.findall(r"```(?:bash|sh)\n(.*?)```", path.read_text(), re.S):
            count += 1
            # bash -n parses only. No cluster/network/substitution execution occurs.
            result = subprocess.run(["bash", "-n"], input=block, text=True, capture_output=True)
            if result.returncode:
                errors.append(f"{path.relative_to(ROOT)}: {result.stderr.strip()}")
            for line in block.replace("\\\n", " ").splitlines():
                # bash -n already validates the whole block, including quoted
                # multiline jq/JSON. Only tokenize legacy Python entry points.
                if not re.match(r"^\s*python3\s+scripts/", line):
                    continue
                tokens = shlex.split(line, comments=True)
                if tokens[:2] == ["python3", "scripts/hf.py"]:
                    try:
                        make_parser().parse_args(tokens[2:])
                    except SystemExit as exc:
                        if exc.code:
                            errors.append(f"{path.relative_to(ROOT)}: invalid hf arguments: {line}")
                if len(tokens) > 1 and tokens[0] == "python3" and tokens[1].startswith("scripts/"):
                    if not (ROOT / tokens[1]).is_file():
                        errors.append(f"{path.relative_to(ROOT)}: missing script {tokens[1]}")
    return count, errors


if __name__ == "__main__":
    count, errors = check()
    print("\n".join(errors) if errors else f"Parsed {count} shell blocks and hf commands; none executed.")
    sys.exit(bool(errors))
