"""Recognising a treatment from the caller's words, in their language (ADR-0026, #131).

The dictionary below is the Mia specification's, entered as a practice would enter it in the
Studio. The rules under test are the specification's: generic words, a brace, a cold-plasma
package and a yellow nail are asked about, never chosen.
"""

from __future__ import annotations

import pytest

from app.agents.config import AgentConfig
from app.workloads.conversational.booking import BookingSession
from app.workloads.conversational.capabilities import resolve
from app.workloads.conversational.recognition import candidates

_IDENTITY = dict(
    practice_name="Podologie Healthy Feet München",
    street="Baumkirchner Straße 19",
    postcode="81673",
    city="München",
    phone="+498941115335",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
)


def _service(key, name, de=(), ru=(), en=(), external_id="", **extra):
    return {
        "key": key,
        "name": name,
        "external_id": external_id,
        "aliases": {"de": list(de), "ru": list(ru), "en": list(en)},
        **extra,
    }


_CATALOGUE = [
    _service(
        "klein",
        "Medizinische Fußpflege – klein",
        external_id="372761",
        de=["Fußpflege", "kleine Fußpflege", "kurze Fußpflege", "kleine Behandlung"],
        ru=["педикюр", "мини-педикюр", "небольшой медицинский педикюр"],
        en=["foot care", "small medical pedicure", "basic foot care"],
    ),
    _service(
        "gross",
        "Medizinische Fußpflege – groß",
        external_id="12937",
        de=["Fußpflege", "große Fußpflege", "komplette Fußpflege", "vollständige Fußbehandlung"],
        ru=["педикюр", "полный медицинский педикюр", "комплексная обработка стоп"],
        en=["foot care", "complete medical pedicure", "full foot treatment"],
    ),
    _service(
        "hornhaut",
        "Hornhaut entfernen",
        de=["harte Haut", "verhornte Fersen"],
        ru=["натоптыши", "огрубевшая кожа", "обработать пятки"],
        en=["callus removal", "hard skin"],
    ),
    _service(
        "warze",
        "Warze abtragen",
        de=["Warze", "Dornwarze", "Plantarwarze"],
        ru=["бородавка", "подошвенная бородавка", "шипица"],
        en=["wart removal", "plantar wart"],
    ),
    _service(
        "nagelpilz",
        "Nagelpilzbehandlung (klassisch)",
        de=["Nagelpilz", "Pilznagel", "gelber Nagel"],
        ru=["грибок ногтей", "жёлтый ноготь"],
        en=["nail fungus treatment", "yellow toenail"],
    ),
    _service(
        "erstberatung",
        "Erstberatung",
        external_id="380625",
        consultation=True,
        de=["Erstgespräch", "Füße anschauen lassen", "gelber Nagel"],
        ru=["первичная консультация", "осмотр", "жёлтый ноготь"],
        en=["initial consultation", "yellow toenail"],
    ),
    _service(
        "spange_1",
        "Spangenbehandlung – 1. Sitzung",
        external_id="344818",
        de=["Nagelspange", "Korrekturspange"],
        ru=["установка скобы", "скоба"],
        en=["nail brace fitting", "nail brace"],
    ),
    _service(
        "spange_folge",
        "Spangenbehandlung – Folgesitzung",
        external_id="344821",
        de=["Nagelspange", "Spangenkontrolle", "Spange nachstellen"],
        ru=["коррекция скобы", "скоба"],
        en=["nail brace follow-up", "nail brace"],
    ),
    _service(
        "kaltplasma_6",
        "Kaltplasma – 6 Sitzungen",
        de=["Kaltplasma", "Sechserpaket"],
        ru=["холодная плазма"],
        en=["cold plasma"],
    ),
    _service(
        "kaltplasma_12",
        "Kaltplasma – 12 Sitzungen",
        de=["Kaltplasma", "Zwölferpaket"],
        ru=["холодная плазма"],
        en=["cold plasma"],
    ),
]
_ROWS = [
    (s["key"], (s["name"], *(w for ws in s["aliases"].values() for w in ws))) for s in _CATALOGUE
]


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        # A precise phrase names one service, in every language and despite the grammar.
        ("Ich hätte gern eine große Fußpflege", ["gross"]),
        ("мне нужен полный медицинский педикюр", ["gross"]),
        ("a small medical pedicure please", ["klein"]),
        ("ich habe eine Dornwarze unter dem Fuß", ["warze"]),
        ("у меня бородавку надо удалить", ["warze"]),
        ("meine Fersen sind ganz verhornt, harte Haut", ["hornhaut"]),
        ("Ich möchte die Spange nachstellen lassen", ["spange_folge"]),
        ("Hühnerauge", []),  # not in this catalogue: nothing, never the nearest thing
        # Generic words are the practice's own ambiguity: the agent must ask.
        ("Ich brauche eine Fußpflege", ["klein", "gross"]),
        ("педикюр", ["klein", "gross"]),
        ("foot care", ["klein", "gross"]),
        ("Ich brauche eine Nagelspange", ["spange_1", "spange_folge"]),
        ("Kaltplasmatherapie", ["kaltplasma_6", "kaltplasma_12"]),
        # A symptom never books the fungal treatment on its own.
        ("ich habe einen gelben Nagel", ["nagelpilz", "erstberatung"]),
        ("у меня жёлтый ноготь", ["nagelpilz", "erstberatung"]),
        ("my yellow toenail", ["nagelpilz", "erstberatung"]),
    ],
)
def test_the_callers_words_match_the_practices_services(said, expected) -> None:
    assert candidates(_ROWS, said) == expected


def test_a_short_word_does_not_match_inside_another() -> None:
    assert candidates([("x", ["на дом"])], "натоптыши дома") == []


def _session() -> BookingSession:
    return BookingSession(
        "recognition", practice=AgentConfig(**_IDENTITY, treatments=_CATALOGUE).to_practice()
    )


def test_several_matches_tell_the_agent_to_ask_and_choose_nothing() -> None:
    session = _session()

    result = session.identify_service("Nagelspange")

    assert [m["service_key"] for m in result["matches"]] == ["spange_1", "spange_folge"]
    assert "Ask the caller which one" in result["agent_notes"][0]
    assert session.draft["service_key"] == ""


def test_an_unlinked_match_is_a_callback_and_no_match_offers_the_consultation() -> None:
    session = _session()

    assert "callback" in session.identify_service("Warze")["agent_notes"][0]
    assert "offer Erstberatung" in session.identify_service("Rückenschmerzen")["agent_notes"][0]


def test_the_tool_is_offered_only_to_an_agent_with_services() -> None:
    assert "identify_service" in resolve(AgentConfig(**_IDENTITY, treatments=_CATALOGUE)).tools
    assert "identify_service" not in resolve(AgentConfig(**_IDENTITY)).tools
