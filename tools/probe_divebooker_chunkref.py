#!/usr/bin/env python3
"""Where does `$3e` point, and what is on the other end?

`programm` on all 492 trips reads `"Program\\n$3e"`. That is a **reference**
into the streamed payload and not the day plan, so `_sites_from_name` is handed
the literal string and finds nothing -- the day-plan reader shipped and reads
nothing. The same shape sits in the fee panel: Amelie's *Extra cost* column is
`"$3f"`, which was read past as a value when the fixture was taken.

Next.js streams its flight payload as labelled lines, and this asks the page
what that labelling actually looks like rather than assuming the format:

* every distinct `$ref` the payload holds, and how many times;
* for the first few, the bytes immediately around `<ref>:` where the chunk
  would be declared, printed raw so the delimiter and any length prefix are
  visible;
* whether the reference resolves inside one `self.__next_f.push` chunk or
  across two, because `payload_parts` joins them and the answer decides
  whether joining is enough.

Writes nothing.

    python3 tools/probe_divebooker_chunkref.py [--vessels aml-hayaty]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liveaboard.scrape import divebooker_com as db  # noqa: E402
from liveaboard.scrape.base import FetchBlocked, PoliteFetcher  # noqa: E402
from probe_divebooker import repair_robots  # noqa: E402

REF = re.compile(r'"\$([0-9a-f]{1,4})"|\\n\$([0-9a-f]{1,4})|\$([0-9a-f]{1,4})(?=["\s])')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=Path("data/divebooker.json"), type=Path)
    parser.add_argument("--vessels", default="aml-hayaty")
    parser.add_argument("--refs", type=int, default=16, help="references to open")
    parser.add_argument("--chars", type=int, default=400, help="bytes to print")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--snapshots", default=Path("data/snapshots"), type=Path)
    args = parser.parse_args()

    vessels = json.loads(args.book.read_text(encoding="utf-8")).get("vessels") or {}
    by_slug = {v.get("slug", k).split("-haz")[0]: v for k, v in vessels.items()}

    fetcher = PoliteFetcher(snapshot_dir=args.snapshots, delay=args.delay)
    for host in (db.HOST, f"www.{db.HOST}"):
        repair_robots(fetcher, host)

    for want in [s.strip() for s in args.vessels.split(",") if s.strip()]:
        record = by_slug.get(want)
        if not record:
            print(f"?? {want}: not in the committed book")
            continue
        path = f"/{record['slug']}-{record['divebooker_id']}"
        try:
            html = fetcher.get(f"https://{db.HOST}{path}").body
        except FetchBlocked as exc:
            print(f"BLOCKED {path}: {exc}")
            continue

        raw = db.FLIGHT.findall(html)
        joined, dropped = db.payload_parts(html)
        print(f"\n{'=' * 72}\n{path}\n{'=' * 72}")
        print(f"  {len(raw)} push chunk(s), joined {len(joined):,} chars, "
              f"{dropped} undecodable")

        refs = collections.Counter(
            next(g for g in m.groups() if g) for m in REF.finditer(joined))
        print(f"\n-- {len(refs)} distinct reference(s), {sum(refs.values())} uses --")
        for ref, n in refs.most_common(12):
            print(f"  {n:>4}  ${ref}")

        # Where the payload declares them. Printed raw, because the delimiter
        # and any length prefix are the whole question and a parser written
        # against a guess is what put us here.
        for ref, _ in sorted(refs.items(), key=lambda kv: int(kv[0], 16))[:args.refs]:
            print(f"\n-- ${ref} declared? --")
            for m in re.finditer(rf'(?<![0-9a-zA-Z]){re.escape(ref)}:', joined):
                at = m.start()
                print(f"     at {at}: {joined[max(0, at - 24):at + args.chars]!r}")
                break
            else:
                print("     no declaration found in the joined payload")
            # And which push chunk it is in, since `payload_parts` joins them.
            for i, chunk in enumerate(raw):
                try:
                    text = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if re.search(rf'(?<![0-9a-zA-Z]){re.escape(ref)}:', text):
                    print(f"     declared in push chunk {i} of {len(raw)}")
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
