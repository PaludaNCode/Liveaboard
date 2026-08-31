#!/usr/bin/env bash
# The commit-and-push tail, in a file rather than inline in action.yml.
#
# Extracted so it can be *tested*. The rebase branch below -- reject, rebase,
# re-derive, amend, retry -- is the one path in this pipeline that had never
# executed: it needs a push to actually lose a race, and four attempts to force
# one on 2026-08-31 all hit the "a rebuild is not news" exit instead, because
# no source had moved that day and a job with nothing to say never reaches the
# push at all. Waiting for a real collision means waiting for a busy day and
# then happening to be watching ([#127]).
#
# So `tests/test_publish_push.py` stages the collision in a sandbox of three
# throwaway repositories and runs this file directly. What that can prove is
# the *sequence*: that a rejected push rebases, re-derives, re-stages, amends
# and retries, and that the derived file which lands is the re-derived one
# rather than the stale copy `-X theirs` would otherwise keep.
#
# `PUBLISH_REDERIVE` is what the test substitutes. It defaults to the real
# promote-and-build, so the action's behaviour is unchanged; the test points it
# at a stand-in that regenerates a derived file from its inputs the same way,
# in a repository that has no dataset in it.
set -euo pipefail
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Unquoted on purpose: `paths` is a list written by a workflow author,
# never by a source, and it has to word-split into several arguments.
# shellcheck disable=SC2086
git add -A $PUBLISH_PATHS

if git diff --cached --quiet; then
  echo "${PUBLISH_NOTHING}"
  exit 0
fi

# A rebuild is not news. `cli build` stamps the page with the minute it
# ran -- deliberately, so two builds an hour apart can be told apart --
# so `site/index.html` differs on every run whether or not any data
# did, and the early exit above could never fire. Seven data jobs a day
# therefore committed seven times a day regardless, each one a line in
# `git log --oneline data/` that moved no price and a deploy that
# published nothing new. The log is this project's price history; a
# commit saying "read today's PADI deals" that read nothing is exactly
# the kind of record it objects to elsewhere.
#
# So: if nothing but the page is staged, and the page differs only by
# that stamp, there is nothing to say. Compared with the stamp
# normalised on both sides rather than by reading the shape of a diff --
# the payload is one enormous line, so a real change and the stamp land
# on it together and no line-wise filter can tell them apart.
if git diff --cached --quiet -- . ':(exclude)site/index.html'; then
  stamp='s/"built":"[^"]*"/"built":"-"/'
  if git show HEAD:site/index.html 2>/dev/null | sed "$stamp" \
       | diff -q - <(sed "$stamp" site/index.html) >/dev/null 2>&1; then
    echo "${PUBLISH_NOTHING} (the page was rebuilt, and says so)"
    git reset --quiet
    exit 0
  fi
fi
# Two fixed tokens, expanded by parameter substitution rather than by
# evaluating the subject. Nothing else in it is interpreted.
subject=${PUBLISH_SUBJECT//\{today\}/$(date -u +%Y-%m-%d)}
subject=${subject//\{sha\}/${GITHUB_SHA:0:7}}
if [ -n "${PUBLISH_HEADLINE}" ]; then
  subject="${subject} — ${PUBLISH_HEADLINE}"
fi
git commit -m "${subject}"

# Anything can land while a crawl runs -- another scheduled job, or a
# merge. A plain push is rejected and the whole run's data is thrown
# away, so rebase and retry instead.
branch="${GITHUB_REF_NAME}"
for attempt in 1 2 3; do
  if git push origin "HEAD:${branch}"; then
    exit 0
  fi
  echo "push rejected (attempt $attempt); rebasing onto origin/${branch}"
  git fetch origin "$branch"
  git rebase -X theirs FETCH_HEAD || { git rebase --abort; exit 1; }

  # A rebase moves the inputs under the derived files, so the derived
  # files have to be made again.
  #
  # `-X theirs` favours the commit being replayed -- ours -- which is
  # right for `data/cabins.json`, the reading this job actually took,
  # and wrong for `data/egypt-2027.json` and `site/index.html`, which
  # are nobody's reading: they are output, built at checkout time from
  # inputs that have since changed underneath. So our copies overwrote
  # a *fresher* dataset and page.
  #
  # It happened on 2026-08-31, on the second and third commits of the
  # day. A capped `cabins.yml` landed a cabin book collected 08-31; the
  # `deals.yml` run already in flight had built its page before that,
  # and its rebase put back a page saying `berths_read: 2026-08-28`
  # while `data/cabins.json` beside it said 08-31. The site published a
  # berth-count date three days stale about its own committed data,
  # which is exactly the kind of quiet wrongness this project exists to
  # catch. `promote --check` was green throughout -- it compares the
  # dataset with its inputs and says nothing about the page -- and the
  # next run healed it, so the window was real, deployed and invisible.
  #
  # `checks` cannot catch this: it runs before the push, and the rebase
  # only happens after the push is rejected. So it is re-derived here,
  # and hard-coded rather than passed in as an input, because nine
  # callers each spelling out their own re-derivation is nine chances
  # for one of them to omit it.
  echo "re-deriving the dataset and page on top of the rebased inputs"
  # Overridable so the sequence can be tested without a dataset in the
  # repository; the default is the real thing, so the action is unchanged.
  eval "${PUBLISH_REDERIVE:-PYTHONPATH=src python3 -m liveaboard.cli promote && PYTHONPATH=src python3 -m liveaboard.cli build}"
  # shellcheck disable=SC2086
  git add -A $PUBLISH_PATHS
  if ! git diff --cached --quiet; then
    git commit --amend --no-edit
  fi
done
echo "::error::could not push after 3 attempts"
exit 1