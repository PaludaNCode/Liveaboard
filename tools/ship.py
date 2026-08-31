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
    python3 tools/ship.py --push -m "..."     # gate, then commit on a branch
    python3 tools/ship.py --merge             # gate, then merge that branch

`--fast` is for the edit-run-edit loop and skips the two slowest things, which
are also the two least likely to be what you just broke: `test_promote_check`
re-promotes the whole dataset five times (20s) and `test_layout` drives
Chromium (13s). It is not a substitute for the gate and says so on the way out.

**`--push` will not commit on `main`.** It branches first, from the message,
and `--merge` brings that branch back with a merge commit. Eleven changes went
straight onto `main` before this existed -- no branch, nothing to review, and
"merge to prod" meaning nothing because the work was already there. A default
that has to be remembered is not a default.
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


TRUNK = "main"


def git(*argv: str) -> str:
    return subprocess.run(["git", *argv], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def run_git(*argv: str) -> int:
    return subprocess.run(["git", *argv], cwd=ROOT).returncode


def branch_name(message: str) -> str:
    """A branch named after the change, from its own commit subject.

    Slugged rather than numbered: a list of `claude/143-2` tells nobody what is
    on them, and the subject is already a sentence somebody wrote about this
    change.
    """
    import re

    subject = message.strip().splitlines()[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", subject).strip("-")[:52].rstrip("-")
    return f"claude/{slug or 'change'}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true",
                    help="skip promote_check and the browser suite (inner loop)")
    ap.add_argument("--push", action="store_true",
                    help="commit and push if the gate passes, on a branch")
    ap.add_argument("--merge", action="store_true",
                    help="merge the current branch into the trunk, behind the gate")
    ap.add_argument("-m", "--message", help="commit message (required with --push)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    if args.push and not args.message:
        ap.error("--push needs -m")
    if args.merge and args.push:
        ap.error("--merge and --push are two steps, not one")

    print(f"gate{' (fast)' if args.fast else ''}:")
    if not gate(args.fast, args.workers):
        print("\nnot shipping: the gate is red")
        return 1

    if args.fast:
        print("\nfast gate passed — this is not the full gate. Run it before pushing.")
        return 0
    if args.merge:
        return merge()
    if not args.push:
        print("\ngate passed")
        return 0

    if not git("status", "--porcelain"):
        print("\nnothing to commit")
        return 0

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == TRUNK:
        # Not a warning, and not a flag to override. Work lands on a branch and
        # comes back through `--merge`, so there is something to look at before
        # it is on the trunk and "merge to prod" is an action rather than a
        # report that it already happened.
        branch = branch_name(args.message)
        print(f"\nbranching: {branch}")
        run_git("checkout", "-q", "-B", branch)

    subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
    subprocess.run(["git", "commit", "-q", "-m", args.message], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", branch], cwd=ROOT, check=True)
    print(f"pushed {git('rev-parse', '--short', 'HEAD')} to {branch}")
    print(f"merge it with: python3 tools/ship.py --merge")
    return 0


def drop_stamp_only_rebuild() -> None:
    """Undo the gate's own rebuild when it changed nothing but the clock.

    `cli build` stamps the page with the minute it ran, so the gate leaves
    `site/index.html` modified on every run whether or not any data did -- and
    `--merge` then refused its own tree as dirty. The publish action already
    settles this the same way and for the same reason: a rebuild is not news.

    Compared with the stamp normalised on both sides, never by reading the
    shape of the diff -- the payload is one enormous line, so a real change and
    the stamp land on it together and no line-wise filter can tell them apart.
    """
    import re

    page = ROOT / "site" / "index.html"
    if git("status", "--porcelain", "--", str(page)) == "":
        return
    committed = subprocess.run(["git", "show", f"HEAD:site/index.html"], cwd=ROOT,
                               capture_output=True, text=True)
    if committed.returncode != 0:
        return
    stamp = re.compile(r'"built":"[^"]*"')
    if stamp.sub("", committed.stdout) == stamp.sub("", page.read_text("utf-8")):
        run_git("checkout", "--", "site/index.html")


def merge() -> int:
    """Bring the current branch back to the trunk, behind the gate."""
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == TRUNK:
        print(f"already on {TRUNK}; nothing to merge")
        return 1
    drop_stamp_only_rebuild()
    if git("status", "--porcelain"):
        print("the working tree is dirty; commit before merging")
        return 1

    # The trunk may have moved -- a scheduled data job commits several times a
    # day. Bring it into the branch first, so a conflict is resolved where the
    # work is rather than on the trunk.
    run_git("fetch", "-q", "origin", TRUNK)
    behind = git("rev-list", "--count", f"HEAD..origin/{TRUNK}")
    if behind != "0":
        print(f"{TRUNK} moved {behind} commit(s) ahead; merging it in first")
        if run_git("merge", "--no-edit", f"origin/{TRUNK}") != 0:
            print("conflict merging the trunk into this branch; resolve it here")
            return 1
        print("gate, against the merged tree:")
        if not gate(False, 6):
            print("\nnot merging: the gate is red against the merged trunk")
            return 1

    run_git("checkout", "-q", TRUNK)
    run_git("merge", "-q", "--ff-only", f"origin/{TRUNK}")
    # `--no-ff` so the branch is visible in the history as a unit of work,
    # rather than eleven commits that look like they were typed onto the trunk.
    if run_git("merge", "--no-ff", "--no-edit", branch) != 0:
        print("conflict; resolve on the trunk or reset and retry")
        return 1
    subprocess.run(["git", "push", "-q", "origin", TRUNK], cwd=ROOT, check=True)
    print(f"\nmerged {branch} into {TRUNK} -> {git('rev-parse', '--short', 'HEAD')}")

    # Tidying up, and it is allowed to fail. Some tokens can push a branch and
    # not delete one -- this environment's returns 403 on the delete -- and the
    # merge has already landed by here, so a failure is untidiness rather than
    # a problem. Reported either way: the first version of this swallowed the
    # error and printed the same success line, which is a tool claiming work it
    # had not done.
    run_git("branch", "-q", "-d", branch)
    if run_git("push", "-q", "origin", "--delete", branch) != 0:
        print(f"could not delete origin/{branch} — it is merged, and stale")
    print("CI runs the same gate; it is not worth waiting on unless it goes red.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
