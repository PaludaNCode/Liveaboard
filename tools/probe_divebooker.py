#!/usr/bin/env python3
"""Find out what divebooker.com actually serves, before anything parses it.

This is step one of adding a third seller, and it is deliberately the *whole*
of step one. Nothing here writes to `data/`, nothing here is imported by the
pipeline, and nothing here decides anything: it fetches a handful of pages and
prints what came back, so the parser that follows is written against bytes
somebody has read rather than against a guess. `docs/sources/liveaboard.com.md`
and `docs/sources/padi.com.md` are both the record of a probe like this one,
and every negative in them is a lead nobody has to follow twice.

Five questions, in the order they have to be answered, because each one decides
whether the next is worth asking:

  0. Is the host reachable from here at all? A development sandbox sits behind
     an egress allowlist, so "connection refused" is a fact about this machine
     and not about the site. Run it on a runner (`.github/workflows/probe.yml`)
     before concluding anything.
  1. What does robots.txt say -- read twice, once as text and once as
     `urllib.robotparser` understands it. Those two can disagree: a blank line
     inside a record orphans every rule after it, which is exactly what
     liveaboard.com's file does, and `can_fetch()` then says yes to everything.
     A yes that comes from a parser quirk is not permission, and this prints
     both readings so nobody quotes the second without seeing the first.
  2. What does the site say it has? Sitemaps are the only inventory that is
     not a guess. Bucketed by path shape, they name the vessel and trip URL
     patterns without anybody inventing one.
  3. What does each entry point return -- server-rendered markup, or an empty
     shell? `scrape.diagnose` already answers this for both existing sources,
     so it answers it here too rather than growing a second vocabulary.
  4. Where is the money? JSON-LD `Offer`/`Product`/`Event` nodes are how both
     current sources are read; if the price is not in the served bytes, the
     next question is which endpoint the page calls for it, and inline `/api/`
     literals plus framework state blobs are where PADI's itinerary endpoint
     was found (`tools/probe_padi_bundle.py`).

Detail pages are **sampled from the sitemap**, never typed in here: a URL
pattern somebody guessed is the thing this probe exists to replace.

Writes nothing but snapshots (gitignored), and makes at most `--max-requests`
requests, paced by the polite fetcher like every other fetch in this project.

    python3 tools/probe_divebooker.py [--sample 3] [--max-requests 25]
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from liveaboard.scrape import diagnose, jsonld  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402

HOST = "www.divebooker.com"

#: Paths worth asking for by name, and no more than that. Everything else this
#: probe reads is discovered -- from robots.txt, from a sitemap, or from the
#: links on a page it already has. A long list of hand-typed paths is a list of
#: guesses, and a guess that 404s teaches nothing about where the page really
#: is.
ENTRY_PATHS = ("/",)

#: Inline state blobs, in the order they are worth finding. A page that ships
#: its data in one of these needs no second request to read a price -- PADI's
#: `window.shop` is exactly this -- and a page that ships none of them is
#: fetching its own data from somewhere, which is question 4b.
STATE_BLOBS = (
    "__NEXT_DATA__",
    "__NUXT__",
    "__INITIAL_STATE__",
    "__APOLLO_STATE__",
    "window.shop",
    "data-page=",
)

API_LITERAL = re.compile(r"""["'](/(?:api|graphql|_next/data)/[A-Za-z0-9/_.\-{}]*)["']""")
SITEMAP_LINE = re.compile(r"^\s*sitemap:\s*(\S+)", re.I | re.M)
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
DISALLOW_LINE = re.compile(r"^\s*disallow:\s*(\S*)", re.I | re.M)
MONEY = re.compile(r"(?:€|\$|£|EUR|USD|GBP)\s?\d[\d.,]{2,}", re.I)


def status_of(url: str, agent: str, timeout: float = 20.0) -> tuple[int | str, dict[str, str], int]:
    """The HTTP status of one URL, the headers worth naming, and the body size.

    `RobotFileParser.read()` swallows this. A 403 on robots.txt leaves it
    holding `disallow_all`, a 404 leaves it holding `allow_all`, and both of
    those reach `can_fetch()` as a confident answer about a file nobody read.
    The first run of this probe reported `robots.txt reachable` and then
    refused every URL including robots.txt itself, which is that hole exactly.

    Truthful identification, the same `USER_AGENT` the crawler uses. If the
    site refuses that, the refusal is the finding -- not an obstacle to route
    around by claiming to be a browser.
    """
    request = urllib.request.Request(url, headers={"User-Agent": agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, dict(response.headers), len(body)
    except urllib.error.HTTPError as exc:
        body = exc.read() or b""
        return exc.code, dict(exc.headers or {}), len(body)
    except Exception as exc:  # noqa: BLE001 - the failure mode is the answer
        return f"{type(exc).__name__}: {exc}", {}, 0


TELLTALE_HEADERS = ("server", "cf-ray", "cf-mitigated", "content-type", "retry-after")


def shape(url: str) -> str:
    """A URL's first two path segments, which is what a pattern looks like.

    Same grouping `diagnose` uses on a page's links, applied to a sitemap:
    `/liveaboard/egypt/*` occurring 60 times is the vessel pattern, and it is
    the site that said so rather than us.
    """
    parts = [p for p in urlparse(url).path.split("/") if p]
    return "/" + "/".join(parts[:2]) + ("/*" if len(parts) > 2 else "")


def stated_disallows(raw: str) -> list[str]:
    """Every path the file's own text refuses, in order, duplicates folded.

    Read off the text rather than out of the parser, because the two can
    disagree and the disagreement is the whole point of question 1.
    """
    seen: list[str] = []
    for path in DISALLOW_LINE.findall(raw):
        if path and path not in seen:
            seen.append(path)
    return seen


def orphaned_rules(rules, agent: str, disallowed: list[str], base: str) -> list[str]:
    """Paths the file refuses and `can_fetch` permits anyway.

    This is the decisive form of the question, and the record count is not:
    liveaboard.com's file states 31 rules and `urllib.robotparser` comes away
    holding two *records*, which looks like it read the file. It did not read
    those rules -- a blank line inside the record orphans everything after it,
    and `can_fetch("/BookingStep1")` answers yes. Counting records misses that
    entirely; asking about the paths the file actually names does not.

    A path here is not a licence. It is the site saying no in a way the library
    fails to repeat, which is a decision for a person to take deliberately and
    write down -- see *robots.txt, and the blank line* in
    docs/sources/liveaboard.com.md for what taking it looks like.
    """
    return [p for p in disallowed
            if rules.can_fetch(agent, base + p if p.startswith("/") else p)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--paths", default=",".join(ENTRY_PATHS),
                        help="entry paths to read, comma separated")
    parser.add_argument("--sample", type=int, default=3,
                        help="detail pages to sample per sitemap bucket")
    parser.add_argument("--buckets", type=int, default=3,
                        help="sitemap path shapes to sample from")
    parser.add_argument("--sitemaps", type=int, default=3,
                        help="sitemap files to open")
    parser.add_argument("--max-requests", type=int, default=25,
                        help="hard ceiling on requests, politeness before curiosity")
    parser.add_argument("--delay", type=float, default=5.0,
                        help="seconds between requests; the default is this "
                             "project's pace for a host nobody has checked")
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    parser.add_argument("--dump-shell", action="store_true",
                        help="print the first 2 KB of each entry page's markup")
    args = parser.parse_args()

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    base = f"https://{args.host}"
    budget = args.max_requests

    def get(url: str):
        nonlocal budget
        if budget <= 0:
            print(f"  (request budget spent; skipped {url})")
            return None
        budget -= 1
        try:
            return fetcher.get(url)
        except FetchBlocked as exc:
            print(f"  BLOCKED {url}: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001 - a dead URL must not end the probe
            print(f"  FAILED  {url}: {type(exc).__name__}: {exc}")
            return None

    print(f"== 0. reachability: {args.host} ==")
    robots_url = f"{base}/robots.txt"

    # Asked directly, before the parser gets a chance to hide the answer.
    for candidate in (robots_url, f"https://{args.host.removeprefix('www.')}/robots.txt"):
        status, headers, size = status_of(candidate, fetcher.user_agent)
        named = ", ".join(f"{key}={value}" for key, value in headers.items()
                          if key.lower() in TELLTALE_HEADERS)
        print(f"  GET {candidate}")
        print(f"      status {status} · {size} bytes{' · ' + named if named else ''}")
        if status == 403:
            print("      403 to this project's own user agent. That is the site")
            print("      declining to be read by a declared bot, and it is an")
            print("      answer rather than an obstacle: nothing here pretends")
            print("      to be a browser to get around it.")

    try:
        rules = fetcher._robots_for(robots_url)
    except FetchBlocked as exc:
        print(f"\n  cannot read robots.txt: {exc}")
        print("\n  VERDICT: nothing below can be answered from here. Either the")
        print("  host is not on this environment's egress allowlist -- in which")
        print("  case run this from .github/workflows/probe.yml, where it is a")
        print("  job -- or the site refused us, which is itself the answer.")
        return 1

    # What the parser came away holding, which is not the same as what it read.
    blanket = ("disallow_all" if getattr(rules, "disallow_all", False) else
               "allow_all" if getattr(rules, "allow_all", False) else "")
    print(f"  parser state after read()        : "
          f"{blanket or 'rules read from the file'}")
    if blanket == "disallow_all":
        print("      set by a 401/403 on robots.txt, so every can_fetch() below")
        print("      is that refusal repeated and not a rule anybody wrote.")
    elif blanket == "allow_all":
        print("      set by a 4xx that is not 401/403 — usually no robots.txt at")
        print("      all, so there is no stated position to obey or breach.")

    print("\n== 1. robots.txt, read twice ==")
    raw = get(robots_url)
    disallowed = stated_disallows(raw.body if raw else "")
    orphans = orphaned_rules(rules, fetcher.user_agent, disallowed, base)
    print(f"  Disallow: paths in the file      : {len(disallowed)}")
    print(f"  of those, can_fetch() says yes   : {len(orphans)}")
    for line in disallowed[:8]:
        print(f"    disallow {line}"
              f"{'   <- can_fetch() says yes anyway' if line in orphans else ''}")
    if orphans:
        print("  MISMATCH: the file refuses paths the parser permits. That is")
        print("  liveaboard.com's blank-line bug, and it means can_fetch() is not")
        print("  permission here. Decide deliberately and write the decision down,")
        print("  the way docs/sources/liveaboard.com.md does.")
    stated_delay = rules.crawl_delay(fetcher.user_agent)
    print(f"  Crawl-delay stated               : {stated_delay if stated_delay else 'none'}")
    print(f"  delay in force for this run      : {fetcher.crawl_delay(robots_url)}s")
    for path in args.paths.split(","):
        url = base + path.strip()
        print(f"  can_fetch {url:<48} {rules.can_fetch(fetcher.user_agent, url)}")

    print("\n== 2. what the site says it has ==")
    declared = SITEMAP_LINE.findall(raw.body) if raw else []
    if not declared:
        declared = [f"{base}/sitemap.xml"]
        print("  robots.txt declares no sitemap; trying /sitemap.xml on spec")
    else:
        for url in declared:
            print(f"  declared: {url}")

    buckets: Counter[str] = Counter()
    samples: dict[str, list[str]] = {}
    queue = list(declared[: args.sitemaps])
    opened = 0
    while queue and opened < args.sitemaps:
        result = get(queue.pop(0))
        if result is None:
            continue
        opened += 1
        body = result.body
        if "<urlset" not in body and "<sitemapindex" not in body:
            print(f"  {result.url}: not XML as served "
                  f"({len(body) / 1024:.0f} KB, starts {body[:40]!r}) — "
                  f"possibly gzipped; skipped rather than guessed at")
            continue
        locs = LOC.findall(body)
        kind = "index" if "<sitemapindex" in body else "urls"
        print(f"  {result.url}: {len(locs)} <loc> ({kind})")
        if kind == "index":
            queue.extend(locs)
            continue
        for loc in locs:
            key = shape(loc)
            buckets[key] += 1
            samples.setdefault(key, []).append(loc)

    if buckets:
        print("\n  URL shapes the sitemap publishes:")
        for key, count in buckets.most_common(12):
            print(f"    {count:>6}  {key:<34} e.g. {samples[key][0][:70]}")
    else:
        print("\n  no per-URL sitemap was read, so there is no inventory to sample "
              "from; section 3 falls back to the entry paths and their links")

    print("\n== 3. what each page returns ==")
    pages = []
    for path in args.paths.split(","):
        result = get(base + path.strip())
        if result is not None:
            pages.append(result)
    for key, _ in buckets.most_common(args.buckets):
        for loc in samples[key][: args.sample]:
            result = get(loc)
            if result is not None:
                pages.append(result)

    for result in pages:
        print(diagnose.describe(result))
        if args.dump_shell:
            print("     --- first 2 KB ---")
            print("     " + result.body[:2048].replace("\n", "\n     "))

    print("\n== 4. where the money is ==")
    for result in pages:
        html = result.body
        priced = [n for n in jsonld.walk_documents(html) if "offers" in n or "price" in n]
        blobs = [name for name in STATE_BLOBS if name in html]
        apis = sorted({m for m in API_LITERAL.findall(html)})
        money = MONEY.findall(html)
        print(f"  ~ {urlparse(result.url).path or '/'}")
        print(f"    json-ld nodes with a price : {len(priced)}")
        print(f"    price-shaped strings       : {len(money)}"
              f"{' (e.g. ' + repr(money[0]) + ')' if money else ''}")
        print(f"    inline state blobs         : {', '.join(blobs) if blobs else 'none'}")
        if apis:
            print(f"    endpoint literals          : {len(apis)}")
            for literal in apis[:6]:
                print(f"      {literal}")
        else:
            print("    endpoint literals          : none in the served bytes")

    print("\n== what this settles, and what it does not ==")
    print("  Settled: whether the host answers us, what robots.txt states and")
    print("  whether the parser agrees, which URL patterns the site publishes,")
    print("  and whether a price is in the served bytes or arrives later.")
    print("  Not settled: which node holds which fact. That is the next probe,")
    print("  pointed at the pattern this one found — and whatever it finds goes")
    print("  into docs/sources/divebooker.com.md in the same commit, negatives")
    print("  included.")
    print(f"\n  requests made: {args.max_requests - budget} of {args.max_requests}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
