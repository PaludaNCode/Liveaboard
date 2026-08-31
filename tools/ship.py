"""One command to get a change from the working tree onto `main`.

Shipping had become eight tool calls and about six minutes of waiting: the full
suite run once per edit, `check`, `promote --check` and `build` run separately
after it, the layout suite run again on its own, and then the push followed by
polling CI in a sleep loop. Most of that is serial for no reason -- the suite is
964 tests over 21 modules and no module needs another to finish first.

So this runs the gate in parallel, and it is the *same* gate CI runs, which is
the point: a green run here means the push will be green, and there is nothing
to sit and watch afterwards.

    python3 tools/ship.py                     # the full gate, nothing else
    python3 tools/ship.py --fast              # the inner loop, no browser
    python3 tools/ship.py --push -m "..."     # gate, then commit and push

`--fast` is for the edit-run-edit loop and skips the two slowest things, which
are also the two least likely to be what you just broke: `test_promote_check`
re-promotes the whole dataset five times (20s) and `test_layout` drives
Chromium (13s). It is not a substitute for the gate and says so on the way out.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# `tests` as well as `src`: `unittest discover` puts the tests directory on the
# path for you and `unittest tests.test_x` does not, so the modules that import
# `published` -- the publication gate -- fail to load without it.
ENV = {"PYTHONPATH": "src:tests"}

# The two heaviest modules run first, so the pool is never left holding one long
# job while the other workers idle. Measured: promote_check 20s, layout 13s, and
# everything else under 4s.
SLOW_FIRST = ["test_promote_check", "test_layout"]
BROWSER = {"test_layout"}


def modules() -> list[str]:
    found = sorted(p.stem for p in (ROOT / "tests").glob("test_*.py"))
    rest = [m for m in found if m not in SLOW_FIRST]
    return [m for m in SLOW_FIRST if m in found] + rest


def run(name: str, argv: list[str]) -> tuple[str, int, float, str]:
    start = time.time()
    done = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                          env={**_environ(), **ENV})
    return name, done.returncode, time.time() - start, (done.stdout + done.stderr)


def _environ() -> dict:
    import os
    return dict(os.environ)


def gate(fast: bool, workers: int) -> bool:
    """Everything CI asserts, in parallel. Returns whether it all passed."""
    # The page first and alone: the layout suite measures what this writes, and
    # `promote --check` is the statement that the committed dataset is this
    # code's output.
    name, code, secs, out = run("build", [sys.executable, "-m", "liveaboard.cli", "build"])
    print(f"  {'ok ' if code == 0 else 'FAIL'} {name:22} {secs:5.1f}s")
    if code != 0:
        print(out)
        return False

    jobs: list[tuple[str, list[str]]] = [
        ("check", [sys.executable, "-m", "liveaboard.cli", "check"]),
        ("promote --check", [sys.executable, "-m", "liveaboard.cli", "promote", "--check"]),
        ("seed reproducible", [sys.executable, str(ROOT / "tools" / "check_seed.py")]),
    ]
    for m in modules():
        if fast and (m in BROWSER or m == "test_promote_check"):
            continue
        jobs.append((m, [sys.executable, "-m", "unittest", f"tests.{m}"]))

    failed: list[tuple[str, str]] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for name, code, secs, out in pool.map(lambda j: run(*j), jobs):
            flag = "ok " if code == 0 else "FAIL"
            print(f"  {flag} {name:22} {secs:5.1f}s")
            if code != 0:
                failed.append((name, out))

    print(f"  {'-' * 34}\n  {len(jobs)} jobs in {time.time() - started:.1f}s "
          f"across {workers} workers")
    for name, out in failed:
        print(f"\n=== {name} ===\n{out.strip()[-4000:]}")
    return not failed


def git(*argv: str) -> str:
    return subprocess.run(["git", *argv], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true",
                    help="skip promote_check and the browser suite (inner loop)")
    ap.add_argument("--push", action="store_true", help="commit and push if the gate passes")
    ap.add_argument("-m", "--message", help="commit message (required with --push)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    if args.push and not args.message:
        ap.error("--push needs -m")

    print(f"gate{' (fast)' if args.fast else ''}:")
    if not gate(args.fast, args.workers):
        print("\nnot shipping: the gate is red")
        return 1

    if args.fast:
        print("\nfast gate passed — this is not the full gate. Run it before pushing.")
        return 0
    if not args.push:
        print("\ngate passed")
        return 0

    if not git("status", "--porcelain"):
        print("\nnothing to commit")
        return 0

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
    subprocess.run(["git", "commit", "-q", "-m", args.message], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=ROOT, check=True)
    print(f"\npushed {git('rev-parse', '--short', 'HEAD')} to {branch}")
    print("CI runs the same gate; it is not worth waiting on unless it goes red.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
