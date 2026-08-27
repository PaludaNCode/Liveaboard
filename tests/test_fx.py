"""Tests for exchange rates.

Every advertised price in the dataset is quoted in dollars and the site shows
euro only, so one number sits underneath every figure on the page. It was a
hardcoded 0.92.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import fetch_fx  # noqa: E402

from liveaboard.money import FxTable, Money  # noqa: E402

# The ECB envelope, trimmed to three currencies but otherwise shaped exactly as
# published: gesmes wrapper, three levels of Cube, the date on the middle one,
# and rates expressed as foreign currency per euro.
ECB = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <gesmes:Sender><gesmes:name>European Central Bank</gesmes:name></gesmes:Sender>
  <Cube>
    <Cube time="2026-08-27">
      <Cube currency="USD" rate="1.0856"/>
      <Cube currency="JPY" rate="163.45"/>
      <Cube currency="GBP" rate="0.85320"/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""


class TestEcbParsing(unittest.TestCase):
    def setUp(self):
        self.quoted_on, self.rates = fetch_fx.parse(ECB)

    def test_the_quote_date_is_read(self):
        self.assertEqual(self.quoted_on, "2026-08-27")

    def test_rates_are_inverted_into_euro(self):
        """The ECB publishes dollars per euro; the dataset needs the reverse."""
        self.assertAlmostEqual(float(self.rates["USD"]), 1 / 1.0856, places=6)

    def test_a_currency_dearer_than_the_euro_inverts_the_other_way(self):
        """Sterling guards against an inversion that only looks right for USD."""
        self.assertGreater(self.rates["GBP"], 1)
        self.assertAlmostEqual(float(self.rates["GBP"]), 1 / 0.85320, places=6)

    def test_currencies_the_site_never_quotes_are_dropped(self):
        self.assertNotIn("JPY", self.rates)

    def test_namespaces_do_not_have_to_be_guessed(self):
        """Matched on local names, so an ECB namespace change is survivable."""
        renamed = ECB.replace(b"2002-08-01/eurofxref", b"2099-01-01/eurofxref")
        self.assertEqual(fetch_fx.parse(renamed)[0], "2026-08-27")


class TestEcbRefusesToGuess(unittest.TestCase):
    """An unrecognised response must fail the run, not produce a rate."""

    def test_a_missing_date_is_refused(self):
        with self.assertRaises(ValueError):
            fetch_fx.parse(ECB.replace(b'time="2026-08-27"', b""))

    def test_a_missing_dollar_rate_is_refused(self):
        """Without it there is no site: every advertised price is in dollars."""
        with self.assertRaises(ValueError):
            fetch_fx.parse(ECB.replace(b'<Cube currency="USD" rate="1.0856"/>', b""))

    def test_a_zero_rate_is_refused_rather_than_dividing(self):
        with self.assertRaises(ValueError):
            fetch_fx.parse(ECB.replace(b'rate="1.0856"', b'rate="0"'))

    def test_an_unparseable_date_is_refused(self):
        with self.assertRaises(ValueError):
            fetch_fx.parse(ECB.replace(b'time="2026-08-27"', b'time="27 August"'))

    def test_an_html_error_page_is_not_mistaken_for_rates(self):
        import xml.etree.ElementTree as ET

        with self.assertRaises((ET.ParseError, ValueError)):
            fetch_fx.parse(b"<html><body>503 Service Unavailable</body></html>")


class TestSourceIsRecognisedAsReal(unittest.TestCase):
    def test_the_ecb_does_not_read_as_a_placeholder(self):
        """A source string matching PLACEHOLDER_SOURCE would keep the warning up."""
        table = FxTable.from_dict(
            {
                "display_currency": "EUR",
                "as_of": "2026-08-27",
                "source": fetch_fx.SOURCE,
                "rates": {"USD": 0.921149},
            }
        )
        self.assertTrue(table.is_sourced)

    def test_the_shipped_fallback_still_reads_as_a_placeholder(self):
        from liveaboard.promote import _default_fx

        self.assertFalse(FxTable.from_dict(_default_fx()).is_sourced)


class TestStaleness(unittest.TestCase):
    """Sourced but no longer moving is a third state, distinct from unsourced."""

    def table(self, as_of: str) -> FxTable:
        return FxTable.from_dict(
            {
                "display_currency": "EUR",
                "as_of": as_of,
                "source": fetch_fx.SOURCE,
                "rates": {"USD": 0.921149},
            }
        )

    def test_a_weekend_gap_is_not_stale(self):
        """The ECB publishes on working days; Monday carries Friday's rate."""
        self.assertFalse(self.table("2026-08-28").is_stale(date(2026, 8, 31)))

    def test_a_week_without_a_refresh_is_stale(self):
        self.assertTrue(self.table("2026-08-01").is_stale(date(2026, 8, 27)))

    def test_age_is_reported_in_days(self):
        self.assertEqual(self.table("2026-08-20").age_days(date(2026, 8, 27)), 7)

    def test_a_stale_rate_is_still_a_sourced_one(self):
        """It came from somewhere real. The page says old, not made up."""
        old = self.table("2026-01-01")
        self.assertTrue(old.is_sourced)
        self.assertTrue(old.is_stale(date(2026, 8, 27)))


class TestConversionUsesTheFetchedRate(unittest.TestCase):
    def test_a_dollar_price_converts_at_the_ecb_rate(self):
        _, rates = fetch_fx.parse(ECB)
        table = FxTable.from_dict(
            {
                "display_currency": "EUR",
                "as_of": "2026-08-27",
                "source": fetch_fx.SOURCE,
                "rates": {"USD": float(rates["USD"])},
            }
        )
        euros, rate = table.to_display(Money(Decimal("1085.60"), "USD"))
        self.assertEqual(rate.source, fetch_fx.SOURCE)
        self.assertAlmostEqual(float(euros.rounded), 1000.0, places=0)

    def test_an_unlisted_currency_raises_rather_than_guessing(self):
        """EGP is not published by the ECB, so it must not silently convert."""
        table = FxTable.from_dict(
            {
                "display_currency": "EUR",
                "as_of": "2026-08-27",
                "source": fetch_fx.SOURCE,
                "rates": {"USD": 0.921149},
            }
        )
        with self.assertRaises(ValueError):
            table.to_display(Money(Decimal("500"), "EGP"))


if __name__ == "__main__":
    unittest.main()
