"""A returning patient keeps their card, whatever alphabet their name arrives in (#135, spec §3.2).

The same name written another way — case, spaces, umlauts, Cyrillic, a German or English
transliteration — is one person; a merely similar name is not. The register is searched whole,
including in the spellings it may hold the name in, and a search too broad to read whole is
refused rather than answered from part of it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.base import Patient
from app.connectors.names import same_name, search_terms
from app.providers.sandbox import SandboxCalendar
from app.providers.thevea import _SEARCH_PAGE_SIZE, TheveaConnector, TheveaError
from app.providers.thevea_calendar import TheveaPracticeCalendar


@pytest.mark.parametrize(
    "a,b",
    [
        ("Дмитрий", "Dmitry"), ("Дмитрий", "Dmitrij"), ("Фадеев", "Fadejew"),
        ("Фадеев", "Fadeev"), ("Fadeyev", "Fadejew"), ("Жуков", "Schukow"),
        ("Чайковский", "Tschaikowski"), ("Хрущёв", "Chruschtschow"), ("Хрущёв", "Khrushchev"),
        ("Елена", "Jelena"), ("Наталья", "Natalja"), ("Ксения", "Xenia"), ("Сергей", "Sergej"),
        ("Müller", "Mueller"), ("Anna-Maria", "anna maria"),
    ],
)
def test_the_same_name_in_another_alphabet_or_scheme_is_one_name(a, b) -> None:
    assert same_name(a, b)


@pytest.mark.parametrize(
    "a,b",
    [("Anna", "Anne"), ("Meier", "Maier"), ("Schmidt", "Schmitt"), ("Ivan", "Iwana"), ("Jan", "Yana")],
)
def test_a_similar_name_is_still_another_person(a, b) -> None:
    assert not same_name(a, b)


def test_a_cyrillic_surname_is_searched_in_latin_both_ways() -> None:
    assert search_terms("Фадеев") == ["Фадеев", "Fadeev", "Fadejew", "fade"]
    assert search_terms("Li") == ["Li"]  # too short for a stem


# --- the voice agent's check, on the sandbox --------------------------------------------------------


def test_a_returning_patient_who_gives_their_name_in_cyrillic_keeps_their_card() -> None:
    calendar = SandboxCalendar()
    card = calendar.create_patient(
        Patient(vorname="Dmitrij", nachname="Fadejew", geburtsdatum="1985-03-14")
    )

    found = calendar.match_patients("Дмитрий", "Фадеев", "1985-03-14")

    assert [c.id for c in found] == [card.id]
    assert calendar.match_patients("Дмитрий", "Фадеев", "1985-03-15") == []  # birth date binds


# --- against thevea ---------------------------------------------------------------------------------


def _thevea(cards_by_term, pages=None, seen=None):
    """A thevea whose patient search answers from `cards_by_term`, or page by page from `pages`."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "patientenUebersicht" not in body.get("query", ""):
            return httpx.Response(200, json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}})
        given = body["variables"]["tabellenInput"]
        if seen is not None:
            seen.append((given["search"], given["currentPage"]))
        if pages is not None:
            nodes, total = pages(given["currentPage"])
        else:
            nodes = cards_by_term.get(given["search"], [])
            total = len(nodes)
        return httpx.Response(
            200,
            json={"data": {"patientUebersicht": {"nodes": nodes, "pageInfo": {"nodesCount": total}}}},
        )

    return TheveaConnector("https://mein.thevea.de", "u", "p", transport=httpx.MockTransport(handler))


def _calendar(connector) -> TheveaPracticeCalendar:
    return TheveaPracticeCalendar(connector, rooms={"MA1": 1, "MA2": 2, "MA3": 3, "MA4": 4})


_FADEJEW = {"id": 4711, "vorname": "Dmitrij", "nachname": "Fadejew", "geburtsdatum": "1985-03-14T00:00:00Z"}


def test_thevea_finds_a_latin_card_for_a_name_given_in_cyrillic() -> None:
    connector = _thevea({"Fadejew": [_FADEJEW]})
    calendar = _calendar(connector)

    found = calendar.match_patients("Дмитрий", "Фадеев", "1985-03-14")

    assert [c.id for c in found] == [4711]


def test_thevea_finds_a_card_written_in_another_latin_scheme_through_the_stem() -> None:
    # The caller's name came through as "Fadeev"; the card says "Fadejew". Neither spelling
    # searches the other — the shared stem does.
    connector = _thevea({"fade": [_FADEJEW]})
    calendar = _calendar(connector)

    assert [c.id for c in calendar.match_patients("Dmitry", "Fadeev", "1985-03-14")] == [4711]


def test_the_import_reuses_a_transliterated_card_too() -> None:
    connector = _thevea({"Fadejew": [_FADEJEW]})

    found = connector.find_patient(
        Patient(vorname="Дмитрий", nachname="Фадеев", geburtsdatum="1985-03-14")
    )

    assert found is not None and found.id == 4711


def test_every_page_of_a_long_answer_is_read() -> None:
    """The card on page two used to be invisible: one page was read, the miss was a duplicate."""
    filler = [
        {"id": i, "vorname": "X", "nachname": "Fadeev", "geburtsdatum": "1970-01-01"}
        for i in range(1, _SEARCH_PAGE_SIZE + 1)
    ]
    target = {**_FADEJEW, "nachname": "Fadeev"}
    seen: list = []
    connector = _thevea({}, pages=lambda page: ([target] if page else filler, len(filler) + 1), seen=seen)
    calendar = _calendar(connector)

    assert [c.id for c in calendar.match_patients("Dmitry", "Fadeev", "1985-03-14")] == [4711]
    assert ("Fadeev", 1) in seen


def test_a_search_too_broad_to_read_whole_is_refused() -> None:
    full = [
        {"id": i, "vorname": "X", "nachname": "Fade", "geburtsdatum": "1970-01-01"}
        for i in range(_SEARCH_PAGE_SIZE)
    ]
    connector = _thevea({}, pages=lambda _page: (full, 10**6))

    with pytest.raises(TheveaError, match="refusing to answer from part"):
        connector.match_candidates("Fadeev")
