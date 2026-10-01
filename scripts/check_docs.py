#!/usr/bin/env python3
"""Parse fenced shell blocks without executing the commands."""
import pathlib
import re
import shlex
import subprocess
import sys

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
                # multiline jq/JSON. Only tokenize optional Python utilities.
                if not re.match(r"^\s*python3\s+scripts/", line):
                    continue
                tokens = shlex.split(line, comments=True)
                if len(tokens) > 1 and tokens[0] == "python3" and tokens[1].startswith("scripts/"):
                    if not (ROOT / tokens[1]).is_file():
                        errors.append(f"{path.relative_to(ROOT)}: missing script {tokens[1]}")
    return count, errors


if __name__ == "__main__":
    count, errors = check()
    print("\n".join(errors) if errors else f"Parsed {count} shell blocks; none executed.")
    sys.exit(bool(errors))
