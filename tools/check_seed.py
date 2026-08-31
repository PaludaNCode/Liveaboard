"""The committed seed must be what `tools/make_seed.py` produces.

Nobody can hand-edit a price into the seed dataset without it showing up here.

It was inline YAML inside `.github/actions/checks`, which meant the one check
that guards a hand-edited price could only ever run in CI -- so the answer
arrived after a push rather than before one. It is a script now, and the gate
runs it beside everything else.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "regenerated.json"
        made = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "make_seed.py"), "--out", str(out)],
            cwd=ROOT, capture_output=True, text=True)
        if made.returncode != 0:
            print(made.stdout + made.stderr)
            return made.returncode
        a = json.loads((ROOT / "data" / "seed" / "egypt-2027.json").read_text("utf-8"))
        b = json.loads(out.read_text("utf-8"))

    # The stamp is the one field that moves without anybody editing anything.
    for payload in (a, b):
        payload.pop("generated", None)
    if a != b:
        print("data/seed/egypt-2027.json is out of sync with tools/make_seed.py")
        return 1
    print("seed matches generator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
