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

import json
import os
import re
import shutil
import subprocess
import sys
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


class TestACarriedReadingIsDatedToday(unittest.TestCase):
    """The other half of the handover, and the half a text guard cannot see.

    `TestTheCarriedInputsLandWhereThePipelineReadsThem` asserts the workflow
    file — that the download names `path: data`, that no upload feeding it
    reaches outside `data/`. Those are the right assertions about the mistake
    that was made, and they would pass again on the next one: what failed for
    four days was the *file on disk*, and the pipeline never looked at it.

    So `publish.yml` runs `tools/check_fresh.py` over the books a fetch
    rewrites whole, and this pins both ends — that the step is there, in front
    of the promote that reads those files, and that the check itself still
    fails on a stale date. It is the one clock-reading assertion in the
    repository, and it has to live in a workflow rather than in the suite:
    `promote` and `render` are pure, so a test comparing committed data against
    today would turn `main` red overnight with nobody having changed anything.
    """

    WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    CHECK = ROOT / "tools" / "check_fresh.py"

    def check(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.CHECK), *args],
                              capture_output=True, text=True)

    def book(self, tmp: str, name: str, payload: object) -> str:
        path = Path(tmp) / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_the_publish_job_checks_before_it_promotes(self) -> None:
        body = (self.WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
        # `assertTrue` rather than `assertIn`/`assertRegex` throughout this
        # class: the haystack is 200 lines of YAML and printing it buries the
        # sentence that says what to do about the failure.
        self.assertTrue(
            "tools/check_fresh.py" in body,
            "publish.yml no longer checks that a carried reading is today's, "
            "so a lost hand-off is a green commit again",
        )
        self.assertLess(
            body.index("tools/check_fresh.py"), body.index("cli promote"),
            "the freshness check runs after the promote that reads those "
            "files, so a stale input is published before it is noticed",
        )

    def test_every_carrying_caller_names_a_book(self) -> None:
        """A new source workflow must decide, rather than inherit silence.

        `itineraries.yml` is the one legitimate `fresh: ""` — it refuses to
        rewrite its book when it added nothing, so most days leave the date
        alone — and it says so where the decision is. What this refuses is a
        caller that carries an artifact and never mentions freshness at all,
        because that reads identically to forgetting.
        """
        callers = 0
        for workflow in sorted(self.WORKFLOWS.glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            if "carry:" not in text or workflow.name == "publish.yml":
                continue
            callers += 1
            with self.subTest(workflow=workflow.name):
                self.assertTrue(
                    re.search(r"(?m)^[ \t]*fresh:[ \t]*\S", text),
                    f"{workflow.name} hands over an artifact without naming "
                    f"the book whose date proves it arrived — name it, or "
                    f'write `fresh: ""` with the reason it cannot',
                )
        self.assertGreaterEqual(callers, 6, "far fewer carrying callers than "
                                            "the pipeline has; pattern stale")

    def test_a_book_dated_today_passes_and_a_stale_one_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = self.book(tmp, "cabins.json", {"collected": "2026-09-03"})
            stale = self.book(tmp, "fx.json", {"retrieved": "2026-08-31"})

            ok = self.check("--today", "2026-09-03", fresh)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

            bad = self.check("--today", "2026-09-03", stale)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("2026-08-31", bad.stdout)

            # And one of each: a run carries several books and the complaint
            # must name the one that is wrong rather than the first it read.
            both = self.check("--today", "2026-09-03", fresh, stale)
            self.assertEqual(both.returncode, 1)
            self.assertIn("fx.json", both.stdout)
            self.assertNotIn("cabins.json", both.stdout)

    def test_a_book_with_no_date_and_a_book_that_never_arrived_both_fail(self):
        """Neither absence may read as a pass.

        A file that states no date cannot be checked, and "cannot be checked"
        is precisely how four days of readings went missing. A file that is not
        there at all is the failure this whole mechanism was written for — the
        artifact unpacking a directory too high left exactly that.
        """
        with tempfile.TemporaryDirectory() as tmp:
            undated = self.book(tmp, "deals.json", {"days": {}})
            self.assertEqual(self.check("--today", "2026-09-03", undated)
                             .returncode, 1)

            missing = str(Path(tmp) / "candidate.json")
            gone = self.check("--today", "2026-09-03", missing)
            self.assertEqual(gone.returncode, 1)
            self.assertIn("did not unpack", gone.stdout)


if __name__ == "__main__":
    unittest.main()
