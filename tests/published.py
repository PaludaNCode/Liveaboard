"""Reading committed data from a test, and the one gate that governs it.

Two kinds of test live in this suite and they answer to different masters.

**Tests over fixtures** ask whether the code is right. They pass or fail on
what is in `src/`, they need no network and no crawl, and a failure is always
a thing somebody must edit.

**Tests over committed data** ask whether what shipped is right. They are the
publication gate -- `test_the_advertised_price_is_the_bottom_of_the_ladder`
caught a real page defect, 36 sailings advertising a berth nobody could buy --
and a failure is often fixed by *fetching*, not by editing.

That difference is why they need separating, and the separation is about
**where in a run they sit**, not about whether they are worth having.

    `cabins.yml`, `refresh.yml` and `itineraries.yml` all ran the whole suite
    as their first step, before fetching anything. So on 2026-08-30 the daily
    refresh re-priced 36 Aggressor sailings when their sale ended; the cabin
    book went two days stale and contradicted them by 49%; and `cabins.yml` --
    the only job that can refresh the cabin book -- ran the tests, failed on
    that contradiction, and exited before its fetch step. **The guard gated the
    fetch that would have cleared the condition the guard was testing for.**

A run that fetches and then refuses to publish is recoverable. One that refuses
to fetch is not, and the more assertions we add over committed data the more
ways there are to lock the fetchers out.

So the data workflows now run the suite twice, and this module is what makes
the two runs different:

    LIVEABOARD_TESTS=code python3 -m unittest discover -s tests   # gates the fetch
    python3 -m unittest discover -s tests                         # gates the commit

The default is everything, so nothing changes for a person running the suite,
for CI, or for a pull request -- the gate exists for the four jobs that fetch,
and only between their first step and their commit.

**Ask for committed data through this module.** The gate is method-level
because the split is: `TestThePayloadShipsOnlyWhatThePageReads` checks both a
rule in `app.js` and the bytes that shipped, and only the second half is a
publication gate. A test that opens `data/` for itself is outside the gate and
would lock the fetchers out again, which `TestTheGateIsComplete` in
`test_dataset.py` refuses.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

GATE = "LIVEABOARD_TESTS"
"""Set to ``code`` to run only the tests that answer to `src/`.

Any other value, or none, runs everything. The default direction matters: a
flag that has to be *remembered* to get the full suite is a publication gate
that quietly stops running.
"""

def code_only(env: Any = None) -> bool:
    """Whether this run gates a fetch rather than a commit.

    Takes its environment so the defaulting can be asserted rather than
    described: the direction is the whole safety property here, and a gate
    that has to be remembered to *disable* is one that quietly stops running.
    """
    return (os.environ if env is None else env).get(GATE) == "code"


CODE_ONLY = code_only()

LIVE = "egypt-2027.json"

PUBLISHED = (
    "egypt-2027.json",
    "candidate.json",
    "archive.json",
    "cabins.json",
    "sales.json",
    "deals.json",
    "fees.json",
    "itineraries.json",
    "padi.json",
    "padi_departures.json",
    "barren.json",
    "CHANGES.md",
    # The structured reports the history view renders. Written by the same
    # command that appends to CHANGES.md, so it is behind the same gate.
    "changes.json",
)
"""The committed files a fetch rewrites, and therefore the ones behind the gate.

Not everything in `data/`. `padi_aliases.json` and `operator_facts.json` are
hand-maintained *inputs* -- a crawl never touches them, so a test asserting
they are consistent can never be cleared by fetching and has no business
blocking one. `data/seed/` is a fixture. The line is "would a fetch change
this", which is the same line as "could this test lock the fetchers out".
"""


def committed(name: str = LIVE) -> Path:
    """The path to a committed data file, or a skip explaining which reason.

    Two of them, and they are the same reason wearing different clothes: this
    run is gating a fetch rather than a commit, or the file has not been
    written on this checkout at all. In both cases the test has nothing to
    assert against, and a skip says so where a pass would not.
    """
    if CODE_ONLY:
        raise unittest.SkipTest(
            f"{GATE}=code: this run gates a fetch, and an assertion about "
            f"committed data must never stop one"
        )
    path = DATA / name
    if not path.exists():
        raise unittest.SkipTest(f"{path} has not been built on this checkout")
    return path


def site_page() -> Path:
    """The committed `site/index.html`, or a skip explaining which reason.

    Published output like the dataset, and gated for the same reason: an
    assertion about what shipped must gate a commit and never a fetch. It lives
    here rather than being opened directly so the gate stays the one door.
    """
    if CODE_ONLY:
        raise unittest.SkipTest(
            f"{GATE}=code: this run gates a fetch, and an assertion about "
            f"what was published must never stop one"
        )
    path = ROOT / "site" / "index.html"
    if not path.exists():
        raise unittest.SkipTest(f"{path} has not been built on this checkout")
    return path


def raw(name: str = LIVE) -> Any:
    """One committed file, parsed."""
    return json.loads(committed(name).read_text(encoding="utf-8"))


def dataset(name: str = LIVE):
    """The committed dataset, loaded and validated."""
    from liveaboard.dataset import Dataset

    return Dataset.load(committed(name))


def page(name: str = LIVE) -> dict[str, Any]:
    """What the committed dataset ships to the browser."""
    from liveaboard.render import build_payload

    return build_payload(dataset(name))


def shipped_payload() -> dict[str, Any]:
    """The payload inside the committed `site/index.html`.

    Not `page()`: that rebuilds one from the dataset alone, and some of what
    the page carries is attached by `render` from files beside it -- the
    structured change reports among them. An assertion about what a visitor
    actually received has to read what was actually sent.
    """
    import json
    import re

    html = site_page().read_text(encoding="utf-8")
    found = re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', html, re.S)
    if not found:
        raise AssertionError("the committed page carries no payload")
    return json.loads(found.group(1).replace("<\\/", "</"))
