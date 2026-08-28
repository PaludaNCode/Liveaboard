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

# A path literal, colons included. The first run of this probe left them out and
# found almost nothing, because the app keeps its URLs in a named route table
# written Django-style -- `diving_in:"/diving-in/:slug/"` -- and a character
# class without ":" truncates every entry in it.
PATH_LITERAL = re.compile(
    r"""['"`](/[a-zA-Z0-9][a-zA-Z0-9/_.:$%{}<>?=&-]{2,90})['"`]"""
)

# That route table itself: name -> path. Worth printing whole rather than
# filtered, because it is the app's own index of everywhere it can go.
ROUTE_ENTRY = re.compile(r"""(?P<name>\w{3,40})\s*:\s*['"](?P<path>/[^'"]{2,90})['"]""")

# What Angular writes when the path is assembled: "/liveaboard/" + e + "/".
CONCAT = re.compile(
    r"""['"`](/[a-zA-Z0-9][a-zA-Z0-9/_:-]{2,60})['"`]\s*\+\s*(\w[\w.$\[\]()]*)"""
)

# Call sites. The URL still appears as a literal somewhere, but PADI's API client
# is called with a *relative* path -- `shop/egypt/hammerhead-ii/itineraries/` --
# so a pattern anchored on a leading slash finds none of them. That is what the
# second runner pass got wrong, and why it reported zero call sites while the
# context dump had the endpoint in it.
CALL_SITE = re.compile(
    r"""(?:\.(?:get|post|put)\(|url\s*:\s*)\s*['"`](?P<url>[a-zA-Z/][^'"`]{3,120})['"`]"""
)

# The relative paths that client is handed, template placeholders included.
API_PATH = re.compile(
    r"""['"`](?P<path>(?:shop|booking|search|itinerar|liveaboard|account)"""
    r"""[a-zA-Z0-9/_${}.:-]{3,90}/)['"`]"""
)

# Where that client gets its prefix. Without this the path is known and
# uncallable, which is exactly where the second pass left off: every obvious
# base -- /api/, /api/v1/, /travel/api/ and five more -- 404s.
BASE_NEEDLES = (
    "baseURL", "baseUrl", "axios.create", "API_URL", "apiUrl", "API_BASE",
    "apiBase", "API_ROOT", "/api", "X-CSRFToken", "fetch(", "XMLHttpRequest",
)

# Any literal that mentions an itinerary at all, path-shaped or not. The endpoint
# may be built from a name rather than spelled out, and this is what shows that.
ITINERARY_LITERAL = re.compile(
    r"""['"`](?P<value>[^'"`]{0,80}itinerar[a-z_]*[^'"`]{0,80})['"`]""", re.I
)

INTERESTING = re.compile(r"itinerar|popup|shop|liveaboard|certificat|experience|trip", re.I)

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
    parser.add_argument("--max-candidates", type=int, default=80)
    parser.add_argument("--include-vendors", action="store_true",
                        help="also read vendors.js (third-party, multi-megabyte)")
    args = parser.parse_args()

    vessel_url = f"https://{HOST}/liveaboard/{args.country}/{args.vessel}/"

    print("=" * 72)
    print("1. bundles, discovered from the pages that load them")
    print("=" * 72)
    status, _, page = get(vessel_url)
    print(f"GET {vessel_url} -> {status}, {len(page)} bytes")
    if status != 200:
        print("  cannot continue without the page")
        return 1

    pages = [page]

    # A vessel page ships liveaboard.js; an itinerary page ships itinerary.js.
    # The popup is opened from the vessel page but *routes* to the itinerary URL,
    # so both bundles are in scope and only one of them is named after the thing
    # we are looking for. The slug is taken from the page rather than supplied,
    # since a slug here means nothing and cannot be guessed.
    slug_match = re.search(
        rf'href="/liveaboard/{re.escape(args.country)}/{re.escape(args.vessel)}/([a-z0-9-]+)/"',
        page,
    )
    if slug_match:
        itinerary_url = f"{vessel_url}{slug_match.group(1)}/"
        status, _, itinerary_page = get(itinerary_url)
        print(f"GET {itinerary_url} -> {status}, {len(itinerary_page)} bytes")
        if status == 200:
            pages.append(itinerary_page)
    else:
        print("  no itinerary link on the vessel page -- reading its bundles only")

    bundles: list[str] = []
    for body in pages:
        for match in SCRIPT_SRC.finditer(body):
            if match.group("url") not in bundles:
                bundles.append(match.group("url"))
    for url in bundles:
        print(f"  {url}")
    if not bundles:
        print("  none found -- SCRIPT_SRC no longer matches the page")
        return 1

    # The feature bundles first. Either is where the app's own code lives, and
    # both are small. vendors.js is third-party and multi-megabyte, so it is
    # skipped unless asked for -- the call we want is PADI's own.
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
    routes: dict[str, str] = {}
    relative_paths: set[str] = set()
    call_sites: set[str] = set()
    itinerary_words: set[str] = set()

    for url in bundles:
        status, content_type, body = get(url)
        name = url.rsplit("/", 1)[-1]
        print(f"\nGET {name}\n  -> {status} {content_type} {len(body)} bytes")
        if status != 200:
            print("  (skipped)")
            continue
        sources[url] = body

        found = {m.group("name"): m.group("path") for m in ROUTE_ENTRY.finditer(body)}
        routes.update(found)
        print(f"  route-table entries: {len(found)}")

        calls = {m.group("url") for m in CALL_SITE.finditer(body) if m.group("url").startswith("/")}
        call_sites |= calls
        print(f"  call sites with a literal path: {len(calls)}")
        for path in sorted(calls):
            print(f"    {path}")

        api_paths = {m.group("path") for m in API_PATH.finditer(body)}
        if api_paths:
            print(f"  relative API paths: {len(api_paths)}")
            for path in sorted(api_paths):
                print(f"    {path}")
            relative_paths.update(api_paths)

        words = {m.group("value") for m in ITINERARY_LITERAL.finditer(body)}
        itinerary_words |= words
        print(f"  literals mentioning an itinerary: {len(words)}")
        for value in sorted(words)[:40]:
            print(f"    {value!r}")

        joins = {(m.group(1), m.group(2)) for m in CONCAT.finditer(body)
                 if INTERESTING.search(m.group(1))}
        if joins:
            print(f"  concatenated URLs: {len(joins)}")
            for head, var in sorted(joins):
                print(f"    {head!r} + {var}")

        if args.dump_context:
            for value in sorted(w for w in words if "/" in w)[:10]:
                index = body.find(value)
                print(f"\n  --- context for {value!r} ---")
                print("  " + body[max(0, index - 260):index + 260].replace("\n", " "))

    print()
    print("=" * 72)
    print("2b. where the API client gets its base URL")
    print("=" * 72)
    for url, body in sources.items():
        name = url.rsplit("/", 1)[-1]
        for needle in BASE_NEEDLES:
            positions = [m.start() for m in re.finditer(re.escape(needle), body)][:4]
            if not positions:
                continue
            print(f"\n  {name}: {needle!r} x{len(positions)}")
            for index in positions:
                window = body[max(0, index - 200):index + 240].replace("\n", " ")
                print(f"    ...{window}...")

    print("\n--- the app's whole route table ---")
    for name, path in sorted(routes.items(), key=lambda kv: kv[1]):
        mark = "  <-- " if INTERESTING.search(path) or INTERESTING.search(name) else "      "
        print(f"{mark}{name:32} {path}")

    if not sources:
        print("\nno bundle could be read -- is this running on a runner?")
        return 1

    print()
    print("=" * 72)
    print("3. calling the candidates")
    print("=" * 72)

    # Every path found anywhere, with the app's own :placeholders filled from the
    # page we just read. Route-table entries are tried too: a named route is
    # still a URL, and one of them may serve the popup its data.
    known = {
        "slug": args.vessel,
        "location": args.country,
        "country": args.country,
        "shop": args.vessel,
        "id": "94466",
        "activityId": "94466",
        "uuid": "",
        "date": "",
    }

    def fill(path: str) -> str | None:
        """Substitute :params, or None when one has no value to substitute."""
        out = path
        for name in re.findall(r":(\w+)", path):
            value = known.get(name)
            if not value:
                return None
            out = out.replace(f":{name}", value)
        return out

    # A relative API path is useless without its prefix, so every path found is
    # crossed with every base worth trying. Eight bases x a handful of paths is
    # still a small number of requests, and it either finds the endpoint or rules
    # the whole family out in one run.
    bases = ("/api/", "/api/v1/", "/api/v2/", "/travel/api/", "/tapi/", "/rest/",
             "/v1/", "/v2/", "/_api/", "/")
    crossed: set[str] = set()
    for path in relative_paths:
        filled = re.sub(r"\$\{[^}]*countrySlug[^}]*\}|\$\{t\}", args.country, path)
        filled = re.sub(r"\$\{[^}]*\}", args.vessel, filled)
        for base in bases:
            crossed.add(f"{base}{filled}")

    seen: set[str] = set()
    candidates: list[str] = []
    for source in (crossed, call_sites, set(routes.values()),
                   {w for w in itinerary_words if w.startswith("/")}):
        for path in sorted(source):
            if not INTERESTING.search(path):
                continue
            filled = fill(path)
            if filled and filled not in seen:
                seen.add(filled)
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
