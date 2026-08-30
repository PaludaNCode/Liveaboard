"""What one trip's itinerary fragment states.

The fixture is markup a probe returned from the live endpoint, trimmed to the
parts anything reads and otherwise verbatim -- minified, with the optional
closing tags the page really omits.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from liveaboard.promote import itinerary_key
from liveaboard.taxonomy import (
    DIVER_LEVEL_BARS,
    DIVER_LEVEL_ORDER,
    DiverLevel,
)
from liveaboard.scrape.itinerary import (
    TripDetail,
    parse_prose,
    min_logged_dives,
    parse_regions,
    parse_trip,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fetch_itineraries  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "itinerary_fragment.html"


def markup() -> str:
    return FIXTURE.read_text(encoding="utf-8")


class TestWhereTheTripGoes(unittest.TestCase):
    """The reason this endpoint is worth fetching at all."""

    def test_the_curated_region_list_is_read(self):
        self.assertEqual(
            parse_regions(markup()),
            ("The Brothers", "Elphinstone", "Daedalus", "Abu Dabab"),
        )

    def test_the_whole_sea_is_not_a_place(self):
        """Every trip here is in the Red Sea, so it selects all 314 rows."""
        self.assertNotIn("Red Sea", parse_regions(markup()))

    def test_both_quoted_and_bare_titles_are_read(self):
        """The minifier quotes an attribute only when it contains a space, so
        `title=Daedalus` sits beside `title="Abu Dabab"` in one list."""
        regions = parse_regions(markup())
        self.assertIn("Daedalus", regions)      # title=Daedalus
        self.assertIn("Abu Dabab", regions)     # title="Abu Dabab"

    def test_the_list_is_found_by_its_heading_not_its_classes(self):
        """Tailwind class strings carry brackets and change with the layout."""
        stripped = markup().replace('class=leading-snug', "").replace(
            'class="relative pl-4.5 py-1 truncate"', "")
        self.assertEqual(len(parse_regions(stripped)), 4)

    def test_markup_without_the_block_yields_nothing(self):
        self.assertEqual(parse_regions("<div>Boat features</div>"), ())
        self.assertEqual(parse_regions(""), ())


class TestTheOverviewRows(unittest.TestCase):
    def setUp(self):
        self.detail = parse_trip(markup())

    def test_a_per_trip_dive_count(self):
        """Stated for this sailing, not for the hull. The dataset's counts were
        per vessel and had to be pinned to one trip length to stay honest."""
        self.assertEqual(self.detail.dives, 18)

    def test_a_per_trip_guest_count(self):
        self.assertEqual(self.detail.guests, 20)

    def test_the_stated_entry_bar(self):
        self.assertEqual(
            self.detail.experience,
            "Advanced Open Water - 50 minimum logged dives required.",
        )

    def test_rows_are_read_through_omitted_closing_tags(self):
        """`<dt>Dives <dd>18` with no `</dt>` is what the page actually sends."""
        self.assertIn("<dt class=text-sm>Dives <dd", markup())
        self.assertEqual(self.detail.dives, 18)

    def test_an_absent_row_is_none_not_zero(self):
        detail = parse_trip("<dl><dt>Country <dd>Egypt</dl>")
        self.assertIsNone(detail.dives)
        self.assertIsNone(detail.guests)

    def test_an_implausible_count_is_rejected(self):
        """A fortnight at four a day is 56; more is a misparse."""
        self.assertIsNone(parse_trip("<dt>Dives <dd>1998 dives in total").dives)
        self.assertIsNone(parse_trip("<dt>Group Size <dd>Up to 900 guests").guests)

    def test_an_empty_fragment_is_an_empty_record(self):
        self.assertFalse(parse_trip(""))
        self.assertFalse(TripDetail())


def other(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


class TestTheOperatorsOwnProse(unittest.TestCase):
    """What the operator writes about where the boat actually goes.

    The richest statement on the fragment and the only one in sentences: a
    "Key regions" list is a summary, this is an account. Kept verbatim --
    nothing here decides which words are places, so improving the site
    vocabulary later does not mean fetching 315 pages again.

    The three shapes below are not hypothetical. They were measured across all
    67 vessels before any of this was written, after a first attempt was
    written against one hand-trimmed fixture and matched nothing on any of
    them.
    """

    def setUp(self):
        self.intro, self.sections = parse_prose(markup())

    def test_the_prose_is_found_past_the_itinerary_map(self):
        """A `<figure>` holding the map, a magnify button and two inline SVGs
        sits between the heading and the prose. Requiring them adjacent is what
        made the first parser match on none of the 67 vessels."""
        self.assertIn("<figure", markup())
        self.assertTrue(self.sections)

    def test_the_lead_paragraph_is_kept(self):
        self.assertIn("northern Red Sea", self.intro)
        self.assertNotIn("<", self.intro)

    def test_each_heading_is_paired_with_its_own_text(self):
        self.assertEqual([s.heading for s in self.sections],
                         ["Day 2", "Day 3", "Day 5", "Day 7"])
        self.assertEqual(self.sections[1].text, "Brothers, overnight stay.")

    def test_the_days_are_not_contiguous(self):
        """2, 3, 5, 7 -- a sketch of the week, not a log of it, and the page
        calls it a Sample Itinerary. Nothing may present it as a guarantee."""
        self.assertNotEqual(len(self.sections), 7)

    def test_a_label_with_nothing_under_it_is_not_a_section(self):
        """"Sample Itinerary" is a bold run exactly like a day heading is."""
        self.assertNotIn("Sample Itinerary", [s.heading for s in self.sections])

    def test_it_says_things_the_region_list_does_not(self):
        """The reason to read it at all. This trip's regions name Abu Dabab
        and not Tobia Kebir; the prose is the other way round. Neither is a
        superset, so one cannot stand in for the other."""
        text = " ".join(s.text for s in self.sections)
        self.assertIn("Tobia Kebir", text)
        self.assertNotIn("Abu Dabab", text)

    def test_a_harbour_appears_in_the_prose(self):
        """"Elphinstone, Port Ghalib, overnight stay in harbour" -- reading
        places out of this is not the same as reading dive sites out of it."""
        self.assertIn("Port Ghalib", self.sections[-1].text)

    def test_a_day_whose_content_is_a_bullet_list(self):
        """Some operators put the day under a `<ul>`, so the content is not a
        paragraph at all. Splitting on paragraphs found none of these."""
        _, sections = parse_prose(other("itinerary_days_bulleted.html"))
        self.assertEqual([s.heading for s in sections], ["Day 1:", "Day 2:"])
        self.assertIn("boarding begins at 5:00 pm", sections[0].text)
        self.assertIn("Marsa Bareika", sections[1].text)

    def test_bullets_do_not_run_into_each_other(self):
        """Tags are replaced by a space, not removed: "5:00 pmThe crew" is
        what deleting them outright produces."""
        _, sections = parse_prose(other("itinerary_days_bulleted.html"))
        self.assertIn("5:00 pm The", sections[0].text)

    def test_some_vessels_describe_places_and_never_a_day(self):
        """Four of the 67 never write "Day": the headings are reefs and the
        text describes them. A parser that assumes days reads nothing here."""
        _, sections = parse_prose(other("itinerary_places_not_days.html"))
        self.assertEqual([s.heading for s in sections],
                         ["Brothers Islands", "Daedalus Reef"])
        self.assertFalse(any(s.is_day for s in sections))

    def test_a_day_heading_is_told_from_a_place_heading(self):
        self.assertTrue(all(s.is_day for s in self.sections))

    def test_markup_without_the_block_yields_nothing(self):
        self.assertEqual(parse_prose("<div>Key regions</div>"), (None, ()))
        self.assertEqual(parse_prose(""), (None, ()))

    def test_a_trip_carries_it(self):
        detail = parse_trip(markup())
        self.assertTrue(detail.sections)
        self.assertTrue(detail.intro)


class TestTheLoggedDiveBar(unittest.TestCase):
    def test_a_stated_number_is_read(self):
        self.assertEqual(
            min_logged_dives("Advanced Open Water - 50 minimum logged dives required."),
            50,
        )

    def test_a_certification_with_no_number_asks_for_none(self):
        """"Advanced Open Water" alone states a certification and no dive
        count. Inventing one would soften a stated safety requirement."""
        self.assertEqual(min_logged_dives("Advanced Open Water required."), 0)
        self.assertEqual(min_logged_dives(None), 0)


class TestTheEntryBarVocabularyIsOrdered(unittest.TestCase):
    """`DIVER_LEVEL_BARS` is the page's ladder, and its order is the rung.

    The Entry bar column sorts on a level's *position* in this list and its
    filter chips are painted in that order, so the list is not a lookup table
    that happens to be written in a tidy sequence -- the sequence is the claim
    "this bar is harder than that one". `DIVER_LEVEL_ORDER` makes the same
    claim for `_strictest`, which is what decides whose bar gets published when
    the two sellers disagree. Two copies of one ordering, and if they drift the
    page sorts by one rule while the dataset was built by the other.
    """

    def test_the_bars_run_in_the_declared_order(self):
        self.assertEqual([level for level, _, _ in DIVER_LEVEL_BARS],
                         DIVER_LEVEL_ORDER)

    def test_every_level_has_a_bar(self):
        """A level with no entry here prints as an empty cell, which the page
        reads as "nobody stated one" -- the one thing a stated safety
        requirement must never be mistaken for."""
        self.assertEqual({level for level, _, _ in DIVER_LEVEL_BARS}, set(DiverLevel))

    def test_the_implied_dive_count_never_falls(self):
        """The dives a level implies rise with the level, because the column
        prints the greater of this and the trip's own number: a ladder whose
        implied count went down at some rung would let a higher certification
        print a lower bar than the rung below it."""
        implied = [dives for _, _, dives in DIVER_LEVEL_BARS]
        self.assertEqual(implied, sorted(implied))

    def test_a_certification_name_states_no_dive_count(self):
        """The two facts are separate fields here precisely so the phrase can
        be built without the "Advanced + 50 dives, 50 logged dives" the old
        rendering produced. A number back in the certification name would
        reintroduce it, and the regex that used to paper over it is gone."""
        for _, cert, _ in DIVER_LEVEL_BARS:
            with self.subTest(cert=cert):
                self.assertFalse(any(c.isdigit() for c in cert), cert)


if __name__ == "__main__":
    unittest.main()


def archive(*events) -> dict:
    """An archive page, shaped as ``data/archive.json`` stores one."""
    return {
        "pages": [
            {
                "url": "https://www.liveaboard.com/diving/egypt/alia-soul?m=5/2027",
                "nodes": [
                    {"@type": "Product", "sku": "LA-1-4418"},
                    *[{"@type": "Event", "@id": i, "name": n} for i, n in events],
                ],
            }
        ]
    }


class TestWhichTripsGetFetched(unittest.TestCase):
    """One request per itinerary. 878 tour ids exist and 314 trips do.

    Every field this book fills has a fallback in ``promote``, so asking for
    the wrong things is not a red build -- it is a page that quietly keeps
    yesterday's answers. These are the tests that would catch it.
    """

    def wanted(self, *events):
        return fetch_itineraries.wanted(archive(*events))

    def test_the_ids_come_out_of_the_event_id(self):
        picked = self.wanted(("LA-1-4418-353504", "North & Tiran (Hurghada - Hurghada)"))
        slug, boat, tour, _ = next(iter(picked.values()))
        self.assertEqual((slug, boat, tour), ("alia-soul", "4418", "353504"))

    def test_the_slug_survives_the_month_query(self):
        """``?m=5/2027`` contains a slash, so splitting the path last gives
        "2027" and every trip is filed under a vessel that does not exist."""
        slug = next(iter(self.wanted(("LA-1-4418-1", "North & Tiran")).values()))[0]
        self.assertEqual(slug, "alia-soul")

    def test_every_sailing_of_one_trip_is_one_request(self):
        picked = self.wanted(
            ("LA-1-4418-1", "North & Tiran (Hurghada - Hurghada)"),
            ("LA-1-4418-2", "North & Tiran (Hurghada - Hurghada)"),
            ("LA-1-4418-3", "North & Tiran (Hurghada - Hurghada)"),
        )
        self.assertEqual(len(picked), 1)

    def test_a_trip_on_sale_is_the_same_trip(self):
        """The banner is on the Event name and promote strips it before
        grouping. Keyed raw, this asks for a trip it already has and files the
        answer under a key no itinerary will ever look up."""
        picked = self.wanted(
            ("LA-1-4418-1", "Ultimate Red Sea (Port Ghalib - Hurghada)"),
            ("LA-1-4418-2", "20% Off: Ultimate Red Sea (Port Ghalib - Hurghada)"),
        )
        self.assertEqual(len(picked), 1)

    def test_two_ports_are_two_trips(self):
        picked = self.wanted(
            ("LA-1-4418-1", "Brothers & Daedalus (Hurghada - Hurghada)"),
            ("LA-1-4418-2", "Brothers & Daedalus (Hurghada - Port Ghalib)"),
        )
        self.assertEqual(len(picked), 2)

    def test_the_key_is_the_one_promote_looks_up(self):
        picked = self.wanted(("LA-1-4418-1", "20% Off: Ultimate Red Sea"))
        self.assertIn(itinerary_key("alia-soul", "Ultimate Red Sea"), picked)

    def test_an_id_in_another_shape_is_skipped_not_guessed(self):
        """A url built from a misread id asks the source for nothing useful."""
        self.assertEqual(self.wanted(("LA-1-4418", "North & Tiran")), {})
        self.assertEqual(self.wanted((None, "North & Tiran")), {})

    def test_a_nameless_event_is_skipped(self):
        """Its key would be the vessel alone, colliding with every other."""
        self.assertEqual(self.wanted(("LA-1-4418-1", "")), {})

    def test_the_endpoint_carries_both_ids_and_asks_for_no_prices(self):
        """Prices come from the Event offers; this is about the trip."""
        url = fetch_itineraries.endpoint("4418", "353504")
        self.assertIn("boatID=4418", url)
        self.assertIn("tourID=353504", url)
        self.assertIn("showPrices=false", url)
