"""The publish tail's rebase branch, driven in a sandbox.

**Why this exists rather than a note saying it was observed once.** The
reject-rebase-re-derive path in `.github/actions/publish/push.sh` needs a push
to actually lose a race, and a race needs two jobs that both have something to
commit. On 2026-08-31 four attempts to force one -- two capped `cabins.yml`
runs, the scheduled `padi.yml` and a dispatched one -- every single time hit the
*other* early exit instead:

    nothing read; nothing to commit (the page was rebuilt, and says so)

because nothing in any source had moved that day. A job with nothing to say
never reaches the push, so it cannot lose a race, so the rebase path stays
unreachable. Waiting for a live collision means waiting for a day something
moves and then happening to be watching it ([#127]).

So the collision is staged here instead: a bare repository standing in for
origin, and two clones standing in for two data jobs. What that proves is the
**sequence** -- rejected push, rebase, re-derive, re-stage, amend, retry -- and
the one thing the sequence exists for: that the derived file which lands is the
*re-derived* one, not the stale copy `-X theirs` keeps by default.

What it deliberately does not prove is that `cli promote` and `cli build`
produce the right dataset; that is `promote --check` and
`TestThePageIsWhatItsDataBuilds`, and it needs the real repository. Here the
re-derivation is a stand-in through `PUBLISH_REDERIVE`, which the script reads
and which defaults to the real commands, so the action's own behaviour is
untouched by the seam.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUSH = ROOT / ".github" / "actions" / "publish" / "push.sh"

#: The stand-in for promote-and-build: `derived` is the concatenation of the
#: inputs, which is all a derived file has to be for this to mean something --
#: it is wrong whenever it does not match the inputs beside it, which is
#: exactly the property the real dataset and page have.
REDERIVE = "cat data/*.txt > derived.txt"


def git(*args: str, cwd: Path, **kw) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(("git",) + args, cwd=cwd, env=env, check=True,
                          capture_output=True, text=True, **kw).stdout


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"),
                     "needs git and bash")
class TestTheRebaseReDerivesRatherThanKeepingItsStaleCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.origin = self.tmp / "origin.git"
        git("init", "--bare", "-b", "main", str(self.origin), cwd=self.tmp)

        seed = self.tmp / "seed"
        seed.mkdir()
        git("init", "-b", "main", cwd=seed)
        (seed / "data").mkdir()
        (seed / "data" / "a.txt").write_text("a1\n")
        (seed / "data" / "b.txt").write_text("b1\n")
        (seed / "derived.txt").write_text("a1\nb1\n")
        git("add", "-A", cwd=seed)
        git("commit", "-m", "seed", cwd=seed)
        git("remote", "add", "origin", str(self.origin), cwd=seed)
        git("push", "origin", "main", cwd=seed)

    def clone(self, name: str) -> Path:
        path = self.tmp / name
        git("clone", str(self.origin), str(path), cwd=self.tmp)
        return path

    def run_push(self, cwd: Path, paths: str = "data derived.txt") -> str:
        """The real script, with the re-derivation swapped for a stand-in."""
        env = {
            **os.environ,
            "PUBLISH_PATHS": paths,
            "PUBLISH_SUBJECT": "data: {today}",
            "PUBLISH_HEADLINE": "",
            "PUBLISH_NOTHING": "nothing changed",
            "PUBLISH_REDERIVE": REDERIVE,
            "GITHUB_REF_NAME": "main",
            "GITHUB_SHA": "0" * 40,
        }
        done = subprocess.run(["bash", str(PUSH)], cwd=cwd, env=env,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0,
                         f"push.sh failed:\n{done.stdout}\n{done.stderr}")
        return done.stdout + done.stderr

    def test_a_rejected_push_rebases_and_the_re_derived_file_wins(self):
        """The whole of #127, in the order it happens.

        `slow` is the job that started first and is holding a `derived.txt`
        built before `fast`'s input existed. That is the stale copy `-X theirs`
        keeps, and the bug: on 2026-08-31 it published a page dated three days
        behind the data committed beside it.
        """
        slow, fast = self.clone("slow"), self.clone("fast")

        # `slow` reads its source and derives, from a tree where b.txt is b1.
        (slow / "data" / "a.txt").write_text("a2\n")
        (slow / "derived.txt").write_text("a2\nb1\n")

        # `fast` lands first, changing an input `slow` has never seen.
        (fast / "data" / "b.txt").write_text("b2\n")
        (fast / "derived.txt").write_text("a1\nb2\n")
        git("add", "-A", cwd=fast)
        git("commit", "-m", "fast", cwd=fast)
        git("push", "origin", "main", cwd=fast)

        log = self.run_push(slow)

        self.assertIn("push rejected", log, "the collision did not happen")
        self.assertIn("re-deriving", log, "the rebase did not re-derive")

        check = self.clone("check")
        self.assertEqual(
            (check / "derived.txt").read_text(), "a2\nb2\n",
            "the published derived file is not what its own inputs build — "
            "this is exactly the berths_read staleness of #127")
        # Neither job's reading was lost on the way.
        self.assertEqual((check / "data" / "a.txt").read_text(), "a2\n")
        self.assertEqual((check / "data" / "b.txt").read_text(), "b2\n")

    def test_an_uncontested_push_neither_rebases_nor_re_derives(self):
        """The ordinary path stays ordinary: no rebase, no second derivation,
        and the commit is the one the job made."""
        solo = self.clone("solo")
        (solo / "data" / "a.txt").write_text("a2\n")
        (solo / "derived.txt").write_text("a2\nb1\n")

        log = self.run_push(solo)

        self.assertNotIn("push rejected", log)
        self.assertNotIn("re-deriving", log)
        check = self.clone("check")
        self.assertEqual((check / "derived.txt").read_text(), "a2\nb1\n")

    def test_nothing_to_commit_never_reaches_the_push(self):
        """The exit that stood between four attempts and a live collision.

        Worth pinning as behaviour rather than remembering as an anecdote: a
        job whose sources have not moved stops here, which is why the rebase
        path cannot be exercised by dispatching jobs on a quiet day.
        """
        quiet = self.clone("quiet")
        before = git("rev-parse", "HEAD", cwd=quiet).strip()

        log = self.run_push(quiet)

        self.assertIn("nothing changed", log)
        self.assertNotIn("push rejected", log)
        self.assertEqual(git("rev-parse", "HEAD", cwd=quiet).strip(), before,
                         "a run with nothing to say made a commit")


if __name__ == "__main__":
    unittest.main()
