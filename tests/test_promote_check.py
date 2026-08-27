"""Tests for ``promote --check``: does the committed dataset match the parser?

The failure this guards against is not a crash. ``PORT_ALIASES`` was merged
with passing tests, folding three spellings of Port Ghalib into one, and the
committed dataset went on holding all three -- because nothing re-promotes
until an unrelated scheduled crawl happens to run. Green build, published site
quietly wrong, no error anywhere.

So the property under test is narrow and specific: promotion is pure, so the
dataset it produces from the committed inputs must equal the committed dataset,
and a difference must be an error rather than a warning nobody reads.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from liveaboard.cli import _describe_drift, main

CANDIDATE = {
    "scraped_at": "2026-08-27",
    "itineraries": [{"id": "alia-soul", "name": "Alia Soul", "boat": "Alia Soul"}],
    "departures": [
        {
            "id": "alia-soul-2027-05-01",
            "boat_slug": "alia-soul",
            "name": "Brothers, Daedalus & Elphinstone (Port Ghalib - Port Ghalib)",
            "start": "2027-05-01",
            "end": "2027-05-08",
            "price": {"amount": 1450.0, "currency": "USD"},
            "provenance": {
                "kind": "scraped",
                "source_id": "liveaboard.com",
                "retrieved": "2026-08-27",
            },
        }
    ],
}

FX = {
    "display_currency": "EUR",
    "as_of": "2026-08-27",
    "source": "European Central Bank euro foreign exchange reference rates",
    "rates": {"USD": 0.92, "GBP": 1.17},
}


class CheckHarness(unittest.TestCase):
    """A temp directory holding a candidate, an FX table and a dataset."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.candidate = self.root / "candidate.json"
        self.fx = self.root / "fx.json"
        self.out = self.root / "egypt-2027.json"
        self.candidate.write_text(json.dumps(CANDIDATE), encoding="utf-8")
        self.fx.write_text(json.dumps(FX), encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def run_cli(self, *extra: str) -> tuple[int, str, str]:
        argv = [
            "promote",
            "--candidate", str(self.candidate),
            "--out", str(self.out),
            "--fx", str(self.fx),
            # Point the optional inputs at paths that do not exist, so the test
            # is not quietly reading the repository's real fee book.
            "--fees", str(self.root / "absent-fees.json"),
            "--facts", str(self.root / "absent-facts.json"),
            *extra,
        ]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def promote_once(self) -> None:
        code, _, err = self.run_cli()
        self.assertEqual(code, 0, err)


class TestPromoteIsDeterministic(CheckHarness):
    def test_promoting_twice_gives_the_same_bytes(self):
        """The check is only meaningful if promotion is reproducible.

        Nothing in the payload may come from the wall clock. ``generated``
        follows the candidate's own ``scraped_at`` and the FX block comes from
        the committed table, so two runs a day apart agree.
        """
        self.promote_once()
        first = self.out.read_text(encoding="utf-8")
        self.out.unlink()
        self.promote_once()
        self.assertEqual(first, self.out.read_text(encoding="utf-8"))


class TestCheck(CheckHarness):
    def test_passes_when_the_dataset_is_current(self):
        self.promote_once()
        code, out, err = self.run_cli("--check")
        self.assertEqual(code, 0, err)
        self.assertIn("matches what promote produces", out)

    def test_writes_nothing(self):
        """--check answers a question; it must not fix the thing it reports."""
        self.promote_once()
        before = self.out.read_bytes()
        stale = json.loads(before)
        stale["itineraries"][0]["port_from"] = "Marsa Ghalib"
        self.out.write_text(json.dumps(stale, indent=2), encoding="utf-8")
        written = self.out.read_bytes()

        code, _, _ = self.run_cli("--check")
        self.assertEqual(code, 1)
        self.assertEqual(self.out.read_bytes(), written)

    def test_fails_when_a_field_drifts(self):
        """The PORT_ALIASES case: the parser folds a port, the dataset does not."""
        self.promote_once()
        stale = json.loads(self.out.read_text(encoding="utf-8"))
        stale["itineraries"][0]["port_from"] = "Ras Galep | Port Ghalib"
        self.out.write_text(json.dumps(stale, indent=2), encoding="utf-8")

        code, _, err = self.run_cli("--check")
        self.assertEqual(code, 1)
        # The message has to name the field and tell the reader what to run,
        # or it is just a red build.
        self.assertIn("port_from", err)
        self.assertIn("Ras Galep | Port Ghalib", err)
        self.assertIn("cli promote", err)

    def test_fails_when_a_departure_disappears(self):
        self.promote_once()
        stale = json.loads(self.out.read_text(encoding="utf-8"))
        stale["departures"] = []
        self.out.write_text(json.dumps(stale, indent=2), encoding="utf-8")

        code, _, err = self.run_cli("--check")
        self.assertEqual(code, 1)
        self.assertIn("departures", err)

    def test_fails_when_there_is_no_committed_dataset(self):
        """Absent is drift too, and saying so beats passing vacuously."""
        code, _, err = self.run_cli("--check")
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)


class TestDescribeDrift(unittest.TestCase):
    """The report has to be readable, or a red build says only "something"."""

    def test_names_the_changed_field(self):
        self.assertEqual(
            _describe_drift({"port_from": "A"}, {"port_from": "B"}),
            ["port_from: 'A' -> 'B'"],
        )

    def test_names_list_entries_by_id_not_index(self):
        """"itineraries[142]" sends a reader counting; the id sends them to the trip."""
        drift = _describe_drift(
            {"itineraries": [{"id": "eagle--north", "nights": 7}]},
            {"itineraries": [{"id": "eagle--north", "nights": 6}]},
        )
        self.assertEqual(drift, ["itineraries[eagle--north].nights: 7 -> 6"])

    def test_reports_a_length_change_once(self):
        """Not once per shifted entry: 878 lines of noise hides the one fact."""
        drift = _describe_drift({"departures": [1, 2, 3]}, {"departures": [1, 2]})
        self.assertEqual(drift, ["departures: 3 entries -> 2"])

    def test_reports_added_and_removed_keys(self):
        self.assertEqual(_describe_drift({}, {"operators": []}), ["operators: added"])
        self.assertEqual(_describe_drift({"operators": []}, {}), ["operators: removed"])

    def test_identical_structures_report_nothing(self):
        payload = {"a": [{"id": "x", "b": 1}], "c": {"d": None}}
        self.assertEqual(_describe_drift(payload, json.loads(json.dumps(payload))), [])

    def test_stops_before_dumping_the_whole_dataset(self):
        """A parser change touching every trip must not print a page per trip."""
        a = {"itineraries": [{"id": f"boat-{n}", "port_from": "A"} for n in range(500)]}
        b = {"itineraries": [{"id": f"boat-{n}", "port_from": "B"} for n in range(500)]}
        self.assertLess(len(_describe_drift(a, b)), 60)


if __name__ == "__main__":
    unittest.main()
