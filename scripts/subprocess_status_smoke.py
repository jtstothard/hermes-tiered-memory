#!/usr/bin/env python3
"""Run a disposable command while making its real status unambiguous.

The marker is emitted by this process, not inferred from a terminal wrapper's
message.  The wrapper exits with the child's status, including re-raising a
signal termination when the child was killed by a signal.
"""

from __future__ import annotations

import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    if not argv:
        print(f"usage: {sys.argv[0]} COMMAND [ARGS...]", file=sys.stderr)
        return 2

    result = subprocess.run(argv, check=False)
    print(f"CHILD_RETURN_CODE={result.returncode}", flush=True)
    if result.returncode < 0:
        os.kill(os.getpid(), -result.returncode)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
