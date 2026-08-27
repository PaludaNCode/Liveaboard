"""Folding dive-site names so two spellings of one reef compare equal.

This module used to infer a route, a theme set and an entry level from the
site list. All three are gone. The page filters on the dive sites themselves,
which is finer-grained than any label over them and cannot be wrong about a
trip the way a label can -- a St John's week spent a while badged as BDE
because two of that route's three reefs outscored one southern one.

Naming a set of sites adds a layer that can be wrong without letting a diver
ask anything the site filter does not already answer. So what is left is the
one piece the rest of the pipeline actually needs: a comparable key.
"""

from __future__ import annotations

import re
import unicodedata


def normalise(name: str) -> str:
    """Fold a dive-site name to a comparable key.

    Egyptian site names arrive transliterated a dozen ways — "Sha'ab", "Shaab",
    "Shaʿb"; "St John's", "St. Johns", "Saint Johns" — so punctuation and
    accents are stripped rather than trusted.
    """
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.lower().replace("&", " and ")
    # Apostrophes are dropped rather than spaced, so "Sha'ab" and "Shaab" agree
    # and "St John's" matches "St Johns". Spacing them apart would split one
    # word into two and quietly break every signature that contains one.
    #
    # The acute accent and the left single quote are in this class because
    # operators type them for an apostrophe: a live title read "St. John´s"
    # (U+00B4) and folded to "st john s", so the St John's route went
    # unrecognised on two of four vessels.
    folded = re.sub(r"['’‘ʿʼ`´]", "", folded)
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()

