#!/usr/bin/env python3
"""Read the vessel-month pages the crawl could not, and say why.

On 2026-08-28 fourteen vessel-month pages came back with no JSON-LD at all --
no ``Event`` nodes and no ``Product`` node -- while forty-two others came back
with a ``Product`` and no ``Event``. The two look identical to a parser that
only asks "how many departures did I get?", and treating them alike deleted 49
real, bookable sailings from the site: DUNE Longara's entire May, still on sale
at the source.

``carry_unread`` now keeps those departures rather than publishing their
absence, which stops the site lying. It does not recover the *current* prices,
and it cannot: it republishes the last reading. So the question this probe
exists to settle, before any parser is written:

1. **Is it transient?** Re-fetch the same URL. If it comes back with its
   JSON-LD, the answer is a retry in the crawl and nothing else.
2. **If it fails again, is the data in the HTML anyway?** The page may render
   its departures in markup while omitting the structured data, in which case a
   markup parser is worth writing. Or the body may be a bot wall, a JS shell,
   or a redirect -- each of which needs a different answer, and none of which is
   a parser.

This writes nothing and parses nothing into the dataset. It exists so that
nobody writes a markup parser for a page nobody has read, which is the rule.

Run from CI; a development sandbox cannot reach the host.

    python3 tools/probe_unread.py [--candidate data/candidate.json] [--retries 2]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from liveaboard.scrape import jsonld  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402

# Greedy, then a colon *and a space*: the URL itself contains colons, and a
# non-greedy match stopped at the one in "https:".
UNPARSED = re.compile(r"^unparsed (\S+): ")

# Shapes worth telling apart in a body with no JSON-LD. None of these is a
# parser; each is a different answer to "what do we do about it?".
TELLS: tuple[tuple[str, str], ...] = (
    ("captcha", "bot wall"),
    ("cf-browser-verification", "Cloudflare interstitial"),
    ("Just a moment", "Cloudflare interstitial"),
    ("Access Denied", "access denied"),
    ("<noscript>", "may need JavaScript"),
)

# Markup that would carry a departure if the page renders them without
# structured data. Deliberately loose: this reports what is there, and a real
# parser gets written afterwards against what this prints.
DEPARTURE_HINTS: tuple[tuple[str, str], ...] = (
    (r"\bm=\d{1,2}/20\d\d", "month links"),
    (r"20\d\d-\d\d-\d\d", "ISO dates"),
    (r"\b\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", "written dates"),
    (r"(US\$|€|\$)\s?\d[\d,]{2,}", "prices"),
    (r"data-tour-?id", "tour ids"),
    (r"sold\s?out", "availability wording"),
)


def unread_urls(candidate: Path) -> list[str]:
    """The pages last run recorded as unreadable, from its own warnings."""
    if not candidate.exists():
        return []
    data = json.loads(candidate.read_text(encoding="utf-8"))
    found = []
    for warning in data.get("warnings", []):
        match = UNPARSED.match(warning)
        if match:
            found.append(match.group(1))
    return sorted(dict.fromkeys(found))


def describe(body: str) -> tuple[int, int, list[str]]:
    """Event nodes, Product nodes, and what the body looks like."""
    try:
        events = len(jsonld.of_type(body, "Event", "TouristTrip", "Trip"))
        products = len(jsonld.of_type(body, "Product"))
    except Exception:  # noqa: BLE001 - a probe must not die on one odd body
        events = products = 0
    notes = [label for needle, label in TELLS if needle.lower() in body.lower()]
    return events, products, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", default=Path("data/candidate.json"), type=Path)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    parser.add_argument("--retries", type=int, default=2,
                        help="fetches per URL, to tell transient from persistent")
    parser.add_argument("--chars", type=int, default=1200,
                        help="body to print when a page has no JSON-LD twice")
    parser.add_argument("--urls", default="",
                        help="comma-separated URLs, instead of the candidate's")
    args = parser.parse_args()

    urls = ([u.strip() for u in args.urls.split(",") if u.strip()]
            or unread_urls(args.candidate))
    if not urls:
        print("no unreadable pages recorded in the last candidate — nothing to probe")
        return 0

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots)
    print(f"{len(urls)} page(s) the last crawl could not read, "
          f"{args.retries} fetch(es) each\n")

    recovered, persistent, blocked = [], [], []

    for n, url in enumerate(urls, 1):
        print("=" * 78)
        print(f"[{n}/{len(urls)}] {url}")
        last_body = ""
        outcome = "no JSON-LD"
        for attempt in range(1, args.retries + 1):
            # The whole question is whether a second *real* request answers
            # differently, and the fetcher's cache would hand back the first
            # body.
            fetcher.forget(url)
            try:
                result = fetcher.get(url)
            except FetchBlocked as exc:
                print(f"    attempt {attempt}: blocked — {exc}")
                outcome = "blocked"
                continue
            except Exception as exc:  # noqa: BLE001 - one bad page must not end the run
                print(f"    attempt {attempt}: failed — {exc}")
                outcome = "error"
                continue
            last_body = result.body
            events, products, notes = describe(result.body)
            print(f"    attempt {attempt}: {len(result.body):>7,} bytes, "
                  f"{events} Event, {products} Product"
                  + (f"  [{', '.join(notes)}]" if notes else ""))
            if events:
                outcome = "recovered"
                break
            if products:
                outcome = "empty month (Product, no Event)"
                break

        if outcome == "recovered":
            recovered.append(url)
            print("    -> reads fine on a retry; this was transient")
            continue
        if outcome == "blocked":
            blocked.append(url)
            continue
        if outcome.startswith("empty month"):
            print("    -> a real empty month, not a failure")
            continue

        persistent.append(url)
        # The part that decides whether a markup parser is even possible.
        hits = [label for pattern, label in DEPARTURE_HINTS
                if re.search(pattern, last_body, re.I)]
        print(f"    -> still no structured data. In the HTML: "
              f"{', '.join(hits) if hits else 'none of the departure markers'}")
        print(f"    --- first {args.chars} chars ---")
        print("    " + last_body[: args.chars].replace("\n", "\n    "))

    print("\n" + "=" * 78)
    print("SUMMARY")
    print(f"  recovered on retry : {len(recovered)}")
    print(f"  still unreadable   : {len(persistent)}")
    print(f"  blocked            : {len(blocked)}")
    for url in persistent:
        print(f"    still unreadable: {url}")
    print()
    if recovered and not persistent:
        print("  Every one read on a retry. The answer is a retry in the crawl,")
        print("  not a markup parser. carry_unread stays as the safety net.")
    elif persistent:
        print("  Some pages answer nothing twice. Read the bodies above before")
        print("  writing anything: what is in them decides whether a markup")
        print("  parser is possible at all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
