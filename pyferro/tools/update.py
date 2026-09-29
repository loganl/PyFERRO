"""Bring the lab PC's clone up to date before the program starts (``pixi run start``).

Runs ``git pull --ff-only`` and says what happened in one line. It never stops the
program from starting: offline, a local edit in the way, no git, or a folder that is
not a git clone each print a note and carry on with the code as it is. ``--ff-only``
means it only ever moves forward to what is on GitHub; it never makes a
merge commit on the lab PC.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT_S = 20  # a slow or absent network must not hold up the start


def git(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # never wait for a password prompt
    return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True,
                          timeout=TIMEOUT_S, env=env)


def main() -> int:
    try:
        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return 0  # not a git clone: nothing to update
        before = git("rev-parse", "HEAD").stdout.strip()
        pulled = git("pull", "--ff-only")
    except FileNotFoundError:
        print("update: git is not installed - starting without updating")
        return 0
    except subprocess.TimeoutExpired:
        print(f"update: no answer from GitHub in {TIMEOUT_S} s - starting without updating")
        return 0

    if pulled.returncode != 0:
        reason = (pulled.stderr or pulled.stdout).strip().splitlines()
        print("update: could not update - starting the code as it is.")
        for line in reason[:4]:
            print(f"  {line}")
        return 0

    after = git("rev-parse", "HEAD").stdout.strip()
    if after == before:
        print("update: already up to date")
        return 0
    log = git("log", "--format=  %h %s", f"{before}..{after}").stdout.rstrip()
    print(f"update: updated to {after[:7]}:\n{log}")
    changed = git("diff", "--name-only", before, after).stdout.split()
    if any(Path(name).name in ("pixi.toml", "pixi.lock") for name in changed):
        print("update: the dependencies changed - close the program and run "
              "'pixi run start' again to install them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
