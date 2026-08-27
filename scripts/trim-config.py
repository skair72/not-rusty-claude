#!/usr/bin/env python3
"""Copy the global Claude config minus some top-level keys, for bisecting.

A hang that only happens with one developer's real ~/.claude.json is bisected
by deleting keys until it stops. Doing that with a shell one-liner cost three
mangled pastes in one session - a terminal wraps a 200-character command and
the newline lands inside a string literal. Hence a file.

    python3 scripts/trim-config.py OUTDIR KEY [KEY...]   # drop these keys
    python3 scripts/trim-config.py OUTDIR --keep KEY...  # keep only these
    python3 scripts/trim-config.py OUTDIR --list         # sizes, change nothing

OUTDIR is a scratch CLAUDE_CONFIG_DIR. Writing into $HOME is refused: the
point is to leave the real config alone, and a bisect that edits its own
input is worthless.
"""

import json
import os
import pathlib
import sys


def source_path():
    """Where the CLI reads the global config from, per the bundle's own rule:
    join(CLAUDE_CONFIG_DIR or homedir(), ".claude.json")."""
    env = os.environ.get("NRC_GLOBAL_CONFIG")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".claude.json"


def main(argv):
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2

    out_dir = pathlib.Path(argv[1]).expanduser().resolve()
    keys = argv[2:]

    src = source_path()
    if not src.is_file():
        print(f"error: no global config at {src}", file=sys.stderr)
        return 1
    data = json.loads(src.read_text())

    if keys == ["--list"]:
        for key, value in sorted(data.items(),
                                 key=lambda kv: -len(json.dumps(kv[1]))):
            print(f"{len(json.dumps(value)):>9} B  {key}")
        return 0

    home = pathlib.Path.home().resolve()
    if out_dir == home:
        print("error: refusing to write into $HOME - pass a scratch dir",
              file=sys.stderr)
        return 1

    if keys[0] == "--keep":
        wanted = set(keys[1:])
        kept = {k: v for k, v in data.items() if k in wanted}
        missing = wanted - set(data)
        action = f"kept {len(kept)} of {len(data)} keys"
    else:
        kept = {k: v for k, v in data.items() if k not in set(keys)}
        missing = set(keys) - set(data)
        action = f"dropped {len(data) - len(kept)} of {len(data)} keys"

    if missing:
        print(f"warning: not present in {src.name}: {', '.join(sorted(missing))}",
              file=sys.stderr)

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / ".claude.json"
    dest.write_text(json.dumps(kept))
    print(f"{action} -> {dest} ({dest.stat().st_size} bytes, was {src.stat().st_size})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
