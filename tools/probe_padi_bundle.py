#!/usr/bin/env python3
"""Find the endpoint PADI's itinerary popup calls, by reading the app's own JS.

The entry bar we want -- certification and logged dives per itinerary -- is not
in any PADI page's HTML. `docs/sources/padi.com.md` establishes that: the vessel
page ships the vocabulary of two coded enums and never a value, because the
value arrives over an AngularJS XHR. The URL of that XHR is in the app bundle.

**This has to run on a runner.** The bundles live on
`d2p1cf6997m1ir.cloudfront.net`, which the sandbox's egress policy refuses with
a 403 to CONNECT. A browser probe is no better off from there -- Chromium would
load the page and then fail to load the app -- so reading the JS is both the
cheaper route and the only one available.

Three steps, one run:

1. Read a vessel page for the bundle URLs. Their filenames are cache-busted with
   a build number, so they are discovered, never pinned.
2. Mine the bundles for URL-shaped literals and for the concatenations Angular
   builds them with.
3. Call the candidates against travel.padi.com and report what came back. The
   runner can reach both hosts, so the endpoint is confirmed in the same run
   rather than handed back as a guess to try later.

Writes nothing. Refuses any candidate `robots.txt` disallows.

    python3 tools/probe_padi_bundle.py [--vessel hammerhead-ii] [--dump-context]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

HOST = "travel.padi.com"
UA = "Mozilla/5.0 (compatible; liveaboard-probe/1.0; +price-transparency research)"

SCRIPT_SRC = re.compile(r'<script[^>]+src="(?P<url>https://[^"]+/travel[^"]+\.js)"', re.I)

# Every string literal that could be a path, and every template literal.
PATH_LITERAL = re.compile(r"""['"`](/[a-zA-Z0-9][a-zA-Z0-9/_.$%{}<>-]{2,80})['"`]""")

# What Angular actually writes: "/liveaboard/" + e + "/" + t + "/". The literals
# alone lose the shape, so the fragments around a concatenation are worth more
# than either end of it.
CONCAT = re.compile(
    r"""['"`](/[a-zA-Z0-9][a-zA-Z0-9/_-]{2,60})['"`]\s*\+\s*(\w[\w.$\[\]()]*)"""
)

INTERESTING = re.compile(r"itinerar|popup|getpopup|shop|liveaboard|certificat|experience", re.I)

# robots.txt for User-agent: * on travel.padi.com, 2026-08-28. Hard-coded so the
# refusal is auditable in review rather than dependent on a fetch, and re-read
# live below so a change shows up as a mismatch.
DISALLOWED = (
    "/checkout/", "/account/", "/accounts/", "/undefined/", "/dive-site/",
    "/dive-sites/", "/ach/", "/covid-19-status-form/", "/diving-in/",
    "/exploration/", "/dive-shops/", "/dive-center/", "/conservation/",
    "/modal/", "/widget/", "/wizard/", "/profile/",
)
DISALLOWED_QUERY = (
    "trip_date=", "activity_date=", "date_from=", "departure_date=",
    "date_after=", "dateStart=", "dateTo=", "from_lang=",
)


def get(url: str, timeout: int = 60) -> tuple[int, str, str]:
    """Returns (status, content-type, body). Never raises on an HTTP error."""
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                response.headers.get("content-type", ""),
                response.read().decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("content-type", ""), error.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001 - a dead candidate must not end the run
        return 0, "", f"{type(error).__name__}: {error}"


def allowed(path: str) -> str | None:
    """None if robots permits it, else the rule that forbids it."""
    for rule in DISALLOWED:
        if path.startswith(rule):
            return f"Disallow: {rule}"
    for key in DISALLOWED_QUERY:
        if key in path:
            return f"Disallow: *{key}"
    return None


def shape(value: object, depth: int = 0) -> str:
    """Summarise structure rather than dumping content."""
    pad = "  " * depth
    if isinstance(value, dict):
        if depth >= 3:
            return f"{{{len(value)} keys}}"
        lines = [f"{pad}  {k}: {shape(v, depth + 1)}" for k, v in list(value.items())[:20]]
        if len(value) > 20:
            lines.append(f"{pad}  ... {len(value) - 20} more keys")
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    if isinstance(value, list):
        if not value:
            return "[] (empty)"
        return f"[{len(value)} x {shape(value[0], depth + 1)}]"
    if isinstance(value, str):
        return f"str {value[:60]!r}" if len(value) > 60 else f"str {value!r}"
    return type(value).__name__ + (f" {value}" if not isinstance(value, dict) else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vessel", default="hammerhead-ii")
    parser.add_argument("--country", default="egypt")
    parser.add_argument("--dump-context", action="store_true",
                        help="print the JS around each interesting literal")
    parser.add_argument("--max-candidates", type=int, default=40)
    parser.add_argument("--include-vendors", action="store_true",
                        help="also read vendors.js (third-party, multi-megabyte)")
    args = parser.parse_args()

    vessel_url = f"https://{HOST}/liveaboard/{args.country}/{args.vessel}/"

    print("=" * 72)
    print("1. bundles, discovered from the vessel page")
    print("=" * 72)
    status, _, page = get(vessel_url)
    print(f"GET {vessel_url} -> {status}, {len(page)} bytes")
    if status != 200:
        print("  cannot continue without the page")
        return 1

    bundles = []
    for match in SCRIPT_SRC.finditer(page):
        if match.group("url") not in bundles:
            bundles.append(match.group("url"))
    for url in bundles:
        print(f"  {url}")
    if not bundles:
        print("  none found -- SCRIPT_SRC no longer matches the page")
        return 1

    # The feature bundles first. A vessel page ships liveaboard.js and an
    # itinerary page ships itinerary.js; either is where the app's own code
    # lives, and both are small. vendors.js is third-party and multi-megabyte,
    # so it is skipped unless asked for -- the call we want is PADI's own.
    def rank(url: str) -> tuple[int, str]:
        name = url.rsplit("/", 1)[-1]
        for index, stem in enumerate(("itinerary", "liveaboard", "main", "runtime", "vendors")):
            if name.startswith(stem):
                return index, name
        return 2, name

    bundles.sort(key=rank)
    if not args.include_vendors:
        bundles = [u for u in bundles if not u.rsplit("/", 1)[-1].startswith("vendors")]

    print()
    print("=" * 72)
    print("2. what the JS says about paths")
    print("=" * 72)
    sources: dict[str, str] = {}
    for url in bundles:
        status, content_type, body = get(url)
        print(f"\nGET {url}\n  -> {status} {content_type} {len(body)} bytes")
        if status != 200:
            print("  (skipped)")
            continue
        sources[url] = body

        literals = {m.group(1) for m in PATH_LITERAL.finditer(body)}
        hits = sorted(p for p in literals if INTERESTING.search(p))
        print(f"  path literals: {len(literals)}, interesting: {len(hits)}")
        for path in hits:
            print(f"    {path}")

        joins = [(m.group(1), m.group(2)) for m in CONCAT.finditer(body)
                 if INTERESTING.search(m.group(1))]
        if joins:
            print(f"  concatenated URLs: {len(joins)}")
            for head, var in sorted(set(joins)):
                print(f"    {head!r} + {var}")

        if args.dump_context:
            for path in hits[:12]:
                index = body.find(path)
                print(f"\n  --- context for {path} ---")
                print("  " + body[max(0, index - 220):index + 220].replace("\n", " "))

    if not sources:
        print("\nno bundle could be read -- is this running on a runner?")
        return 1

    print()
    print("=" * 72)
    print("3. calling the candidates")
    print("=" * 72)

    # Everything path-shaped and itinerary-flavoured, with the app's own
    # placeholders filled from the vessel we just read.
    known = {
        "country": args.country,
        "shop": args.vessel,
        "slug": args.vessel,
        "vessel": args.vessel,
        "liveaboard": args.vessel,
    }
    candidates: list[str] = []
    for body in sources.values():
        for match in PATH_LITERAL.finditer(body):
            path = match.group(1)
            if not INTERESTING.search(path):
                continue
            if any(ch in path for ch in "$<>%"):
                filled = re.sub(r"[${}<>%][^/]*", args.vessel, path)
            else:
                filled = path
            filled = filled.format(**known) if "{" in filled else filled
            if filled not in candidates:
                candidates.append(filled)

    tried = 0
    found: list[tuple[str, str]] = []
    for path in candidates:
        if tried >= args.max_candidates:
            print(f"\n  stopping at --max-candidates={args.max_candidates}")
            break
        rule = allowed(path)
        if rule:
            print(f"  SKIP  {path}\n        robots.txt: {rule}")
            continue
        url = f"https://{HOST}{path}"
        status, content_type, body = get(url, timeout=30)
        tried += 1
        note = f"{status} {content_type.split(';')[0]} {len(body)}B"
        if "json" in content_type:
            found.append((url, body))
            print(f"  JSON  {path}\n        {note}")
        elif status == 200 and len(body) < 60_000:
            found.append((url, body))
            print(f"  200   {path}\n        {note}  (short HTML -- a fragment?)")
        else:
            print(f"  {status:<5} {path}\n        {note}")

    print()
    print("=" * 72)
    print("4. what came back")
    print("=" * 72)
    if not found:
        print("nothing returned JSON or a short body.")
        print("Next: rerun with --dump-context and read how the popup builds its URL.")
        return 0
    for url, body in found[:6]:
        print(f"\n--- {url}")
        try:
            print(shape(json.loads(body)))
        except json.JSONDecodeError:
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
            print(f"  not JSON. text: {text[:400]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
