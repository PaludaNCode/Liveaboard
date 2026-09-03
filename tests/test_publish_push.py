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
import re
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
class TestTheCarriedInputsLandWhereThePipelineReadsThem(unittest.TestCase):
    """The handover has to arrive in `data/`, or the fetch is thrown away.

    Every fetching job uploads paths under `data/`, so `upload-artifact@v4`
    takes `data` as the least common ancestor and makes it the root of the
    archive: an artifact of `data/candidate.json` contains `candidate.json`.
    Downloaded without a `path:`, that unpacks at the workspace root, and
    `promote` goes on reading the committed `data/candidate.json` beside it.

    Which is not a hypothetical. Between 2026-08-31, when the shared tail was
    introduced, and 2026-09-03, **not one carried input changed**: the crawl's
    candidate stood at 08-30 while four daily refreshes, the PADI read, the
    deals read and the cabin read all reported success. `git add data site`
    found the tracked inputs untouched and the stray copies outside its two
    paths, so each run committed a rebuild of the same dataset under
    "no change to trips, prices or availability".

    Nothing caught it and nothing could: from the outside a discarded reading
    and a quiet day are the same commit. The three guards that police this
    contract all ask whether a data commit *reaches the page*, and this one
    did -- with stale data in it.

    Both halves are asserted, because the fix is only correct while the
    assumption under it holds: the download must name `data`, and every upload
    feeding it must stay inside `data/`. An upload that adds a path outside it
    would move the archive's root to the repository root and make `path: data`
    wrong in the same silent direction.
    """

    WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

    #: An `upload-artifact` step and the paths it lists. The artifact name
    #: runs to the end of the line rather than to the first space: every
    #: one of them ends in `${{ github.run_id }}`, and a `\S+` name stopped
    #: at the first brace and matched no step at all -- this guard passed
    #: on zero blocks before that was noticed, which is the same shape of
    #: green-for-nothing it exists to prevent.
    UPLOAD_BLOCK = re.compile(
        r"name:[ \t]*([^\n]+?)[ \t]*\n"
        r"[ \t]*path:[ \t]*\|[ \t]*\n"
        r"((?:[ \t]+[^\s#][^\n]*\n)+)"
    )

    def publish(self) -> str:
        return (self.WORKFLOWS / "publish.yml").read_text(encoding="utf-8")

    def test_the_download_unpacks_into_data(self) -> None:
        body = self.publish()
        step = body[body.index("actions/download-artifact"):]
        step = step[: step.index("- name:", 1)] if "- name:" in step[1:] else step
        self.assertRegex(
            step, r"(?m)^[ \t]*path:[ \t]*data[ \t]*$",
            "publish.yml downloads the carried inputs without `path: data`, so "
            "they unpack at the workspace root and promote reads the committed "
            "copies instead",
        )

    def test_every_carried_upload_stays_inside_data(self) -> None:
        """What `path: data` depends on. The artifact's root is the least
        common ancestor of what was uploaded, so one path outside `data/`
        silently moves it and the download lands a directory too high.

        Counts what it inspected and asserts the count, because the first
        version of this matched nothing: every artifact name ends in
        `${{ github.run_id }}` and the pattern stopped at the first space.
        A guard over an empty set is greener than one over a broken pipeline.
        """
        checked = 0
        for workflow in sorted(self.WORKFLOWS.glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            if "carry:" not in text or workflow.name == "publish.yml":
                continue
            carried = re.findall(r"carry:[ \t]*([^\n]+)", text)
            for block in self.UPLOAD_BLOCK.finditer(text):
                # The upload feeding the tail is the one whose artifact name is
                # the `carry:` value; the snapshots upload is evidence, not
                # input, and its paths may sit anywhere.
                if not any(block.group(1) == c.strip() for c in carried):
                    continue
                for line in block.group(2).strip().splitlines():
                    # The block runs on into the step's next key, which is
                    # indented the same as a path entry. A path is a bare
                    # value; `retention-days: 1` is not.
                    if re.match(r"[\w-]+:", line.strip()):
                        continue
                    checked += 1
                    with self.subTest(workflow=workflow.name, path=line.strip()):
                        self.assertTrue(
                            line.strip().startswith("data/"),
                            "an upload feeding publish.yml reaches outside "
                            "data/, which moves the artifact root and makes "
                            "`path: data` land the inputs in the wrong place",
                        )
            self.assertTrue(
                checked, f"{workflow.name} carries inputs and this guard found "
                f"none of them; the pattern has stopped matching"
            )
        self.assertGreaterEqual(checked, 3, "far fewer carried paths than the "
                                            "pipeline has; the pattern is stale")


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
