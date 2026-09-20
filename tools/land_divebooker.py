#!/usr/bin/env python3
"""Reassemble what a runner printed, because nothing else can carry it.

`fetch_divebooker.py --emit` prints its book, and two pages' JSON-LD, as
gzip+base64 between markers. This turns a saved job log back into files.

Why a log. The development sandbox reaches neither divebooker.com nor the blob
host GitHub serves artifacts from — both refused at the CONNECT, measured — so
the runner is the only thing that can read this source and the log is the only
thing that comes back. A job log persists nothing and writes nothing, which is
also why it is the right channel rather than a workaround.

    python3 tools/land_divebooker.py run.log --out data/
"""

from __future__ import annotations

import argparse
import base64
import gzip
import re
from pathlib import Path

BEGIN = re.compile(r"-----BEGIN (?P<name>[\w.\-]+)-----")
END = re.compile(r"-----END (?P<name>[\w.\-]+)-----")
#: GitHub prefixes every log line with an ISO timestamp; a pasted log keeps it.
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--out", type=Path, default=Path("."))
    args = parser.parse_args()

    name: str | None = None
    chunks: list[str] = []
    written = 0

    for raw in args.log.read_text(encoding="utf-8", errors="replace").splitlines():
        line = STAMP.sub("", raw).strip()
        start = BEGIN.match(line)
        if start:
            name, chunks = start.group("name"), []
            continue
        if name is None:
            continue
        if END.match(line):
            payload = gzip.decompress(base64.b64decode("".join(chunks)))
            target = args.out / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            print(f"  {target}  {len(payload)} bytes")
            written += 1
            name = None
            continue
        chunks.append(line)

    if name is not None:
        print(f"REFUSED: {name} has no END marker in this log — it was "
              f"truncated, and half a file is worse than none")
        return 1
    print(f"{written} file(s) landed")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
