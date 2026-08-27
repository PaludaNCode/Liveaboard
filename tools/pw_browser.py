"""Which Chromium the browser-driven tools should launch.

Four tools need a browser -- the weekly fee scrape and three probes -- and
three of them used to answer this question with the same copied line:

    CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    executable = args.executable or (CHROMIUM if Path(CHROMIUM).exists() else None)

A version number nobody chose, in three places. On CI it was dead: the runner
installs ``chromium-1234`` under ``~/.cache/ms-playwright``, the pinned path
does not exist, and Playwright resolved its own browser. In the sandbox it was
live and wrong -- ``chromium-1194`` is there, so the pin won, and every run
launched Chromium 141 while the installed Playwright expected build 1234.

So the rule here is: **Playwright decides.** It is the only thing that knows
which build it installed and what protocol version it speaks. The two
exceptions are narrow and both say so out loud:

1. ``--executable``, for someone who genuinely needs a specific build.
2. A versionless fallback under ``PLAYWRIGHT_BROWSERS_PATH``, used only when
   Playwright's own binary is missing. That path is a symlink the environment
   maintains beside whatever it installed, so it tracks the environment rather
   than pinning a number -- which is the whole difference from the constant it
   replaces.

Nothing here is silent. :func:`resolve` returns the reason along with the path,
and every caller prints it, because "which browser did that run use" is the
question a stale-browser bug turns on.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def resolve(playwright: Any, override: str | None = None) -> tuple[str | None, str]:
    """Pick the Chromium binary to launch.

    Returns ``(executable_path, reason)``. A ``None`` path means "say nothing to
    Playwright and let it use its own", which is the normal and preferred case
    -- not a failure.

    Takes the live ``playwright`` object rather than guessing from the
    filesystem, because ``chromium.executable_path`` is the installed
    Playwright's own answer and no amount of directory listing reproduces it.
    """
    if override:
        return override, f"--executable {override}"

    managed = getattr(playwright.chromium, "executable_path", None)
    if managed and Path(managed).exists():
        return None, f"Playwright's own browser ({managed})"

    # Playwright's build is missing. Before giving up, look for the versionless
    # symlink the environment keeps beside its install. Deliberately checked
    # second: preferring it would reintroduce the bug this module exists to
    # remove, quietly launching an older build than the one Playwright expects.
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if root:
        fallback = Path(root) / "chromium"
        if fallback.exists():
            return (
                str(fallback),
                f"{fallback} (PLAYWRIGHT_BROWSERS_PATH fallback; "
                f"Playwright expected {managed}, which is not installed)",
            )

    # Nothing found. Still return None: Playwright's own error message names the
    # missing build and tells the reader to run `playwright install`, which is
    # more useful than anything this function could invent.
    return None, f"Playwright's own browser ({managed}) -- which appears not to be installed"
