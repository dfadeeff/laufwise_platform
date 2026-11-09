"""A settlement made after its deadline (ADR-0042 step 5c, § 556 Abs. 3 Satz 3 BGB): the assessment
says per position whether the landlord is responsible for the delay and from when he could have
billed it; the code computes what is lost, what stays claimable and by when, what is paid or
refunded; the check re-derives it. Made-up figures on NK-01's flat, not an expert's case: the 2025
settlement made in 2027, the property tax raised by a notice received later."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from app.kernel.drafts import amounts_not_in
from betriebskosten.calculate import settle
from betriebskosten.verify import verify
from betriebskosten.workflow import _drafts, _parse
from sdk import plain
from test_betriebskosten_gold import CATALOGUE, SETTINGS, _raw
from test_betriebskosten_lease import _assessed

TAX = "U2-01"  # the property tax, raised by a notice received on 20.01.2027


def _case(today: str = "2027-02-15", advance: str = "150.00", late: bool = True) -> dict:
    raw = _raw("nk01")
    every = {p["category"] for p in raw["positions"] if p.get("category")}
    raw = _assessed(raw, every, kind="reference_to_betrkv")
    raw["today"] = today
    raw["tenancies"][0]["monthly_advance"] = advance
    basis = {"source": "BGB", "quote": "es sei denn, der Vermieter hat die verspätete Geltendmachung nicht zu vertreten"}
    if late:
        raw["assessment"]["late_billing"] = {
            "positions": [
                {"position_id": p["id"], "responsible": p["id"] != TAX,
                 "could_bill_from": "2027-01-20" if p["id"] == TAX else None, "basis": basis}
                for p in raw["positions"]
            ],
            "damages_claim": {"possible": True, "basis": basis},
            "lesson": {"text": "Rechtzeitig selbst abrechnen.", "basis": basis},
        }
    return raw


def _settled(raw: dict):
    parsed = _parse(raw, SETTINGS)
    return parsed, settle(parsed.case, CATALOGUE, parsed.policy)


def test_the_late_claim_is_lost_and_what_the_landlord_could_not_bill_earlier_stays_claimable() -> None:
    parsed, s = _settled(_case())
    (st,) = s.statements
    assert (st.total, st.advances, st.balance) == (Decimal("2331.40"), Decimal("1800.00"), Decimal("531.40"))
    assert (st.claimable, st.lost, st.payable) == (Decimal("186.40"), Decimal("345.00"), Decimal("186.40"))
    assert next(line for line in st.lines if line.position_id == TAX).claimable == Decimal("186.40")
    (claim,) = s.late.claims
    assert (claim.position_id, claim.could_bill_from, claim.claim_by) == (TAX, date(2027, 1, 20), date(2027, 4, 20))
    assert [r.priority for r in s.late.recommendations] == [1, 1, 2, 3]
    assert "345,00 €" in s.late.for_a_person[0] and s.late.lesson == "Rechtzeitig selbst abrechnen."
    assert verify(parsed.case, s, CATALOGUE) == []


def test_a_credit_is_refunded_whatever_the_deadline_with_the_later_claim_set_against_it() -> None:
    _, s = _settled(_case(advance="200.00"))  # 2.400,00 € paid in advance
    (st,) = s.statements
    assert (st.lost, st.claimable, st.payable) == (Decimal("0.00"), Decimal("186.40"), Decimal("-68.60"))
    assert s.late.for_a_person == ()  # nothing lost, nothing to claim from anyone


def test_once_the_three_months_have_passed_nothing_stays_claimable() -> None:
    _, s = _settled(_case(today="2027-05-01"))
    (st,) = s.statements
    assert (st.claimable, st.lost, st.payable) == (Decimal("0.00"), Decimal("531.40"), Decimal("0.00"))
    assert s.late.claims == ()


def test_a_settlement_in_time_is_settled_as_always() -> None:
    _, s = _settled(_case(today="2026-12-01"))
    (st,) = s.statements
    assert s.late is None and st.payable is None and st.balance == Decimal("531.40")


def test_late_without_the_assessment_of_who_is_responsible_is_a_question() -> None:
    _, s = _settled(_case(late=False))
    assert not s.ready and any("zu vertreten" in q for q in s.questions)


def test_the_letters_claim_only_what_is_claimable_and_say_why_the_rest_is_not() -> None:
    parsed, s = _settled(_case())
    (letter,) = _drafts(parsed, s)
    assert "lief am 31.12.2026 ab" in letter.text and "345,00 € aus dieser Abrechnung machen wir deshalb nicht geltend" in letter.text
    assert "Nachbelastung (Grundsteuer" in letter.text and "186,40 € innerhalb von 30 Tagen" in letter.text
    result = {k: v for k, v in plain(s).items() if k not in ("questions", "assumptions")}
    assert amounts_not_in(letter.text + "\n" + letter.enclosure, result) == []
    parsed, s = _settled(_case(advance="200.00"))
    (letter,) = _drafts(parsed, s)
    assert "Ihr Guthaben von 68,60 € erstatten wir" in letter.text and "darin verrechnet" in letter.text


def test_a_heating_bill_without_its_split_is_no_split_to_make() -> None:
    # Seen on NK-04 with the real model: the bill named, its base share not given, read as "".
    raw = _case()
    raw["heating"] = {"position_id": "U1-02", "base_share": "", "readings": [], "hot_water_share": None}
    _, s = _settled(raw)
    assert s.ready and s.statements[0].total == Decimal("2331.40")


def test_the_check_refuses_a_late_settlement_the_assessment_does_not_give() -> None:
    parsed, s = _settled(_case())
    (st,) = s.statements
    claimed = dataclasses.replace(s, statements=(dataclasses.replace(st, payable=Decimal("531.40")),))
    assert any("payable" in p for p in verify(parsed.case, claimed, CATALOGUE))


def test_a_late_statement_of_the_owners_association_never_excuses_whatever_the_model_judged() -> None:
    # Seen on NK-04 with the real model: the WEG statement came late, and once in two runs the model
    # judged the landlord not responsible. BGH VIII ZR 249/15 says he is: the model gives the fact,
    # the code applies the law.
    raw = _case()
    for p in raw["assessment"]["late_billing"]["positions"]:
        p.update(responsible=False, could_bill_from="2027-01-20", obstacle="weg_abrechnung")
    parsed, s = _settled(raw)
    (st,) = s.statements
    assert (st.claimable, st.lost, st.payable) == (Decimal("0.00"), Decimal("531.40"), Decimal("0.00"))
    assert s.late.claims == () and verify(parsed.case, s, CATALOGUE) == []
    raw["assessment"]["late_billing"]["positions"][0]["obstacle"] = "bescheid"  # a later notice still excuses
    _, s = _settled(raw)
    assert [c.position_id for c in s.late.claims] == [raw["positions"][0]["id"]]
