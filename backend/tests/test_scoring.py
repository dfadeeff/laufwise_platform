"""Scoring an answer against an expert's scoring sheet (ADR-0042 step 1).

The judge here is scripted. What is proved is the scorer around any judge: a verdict counts only
with a passage quoted from the answer, a knock-out criterion not met makes an answer unusable,
plain checks decide without a model, and points are summed against the expert's threshold.
"""

from __future__ import annotations

import asyncio

import pytest

from app.evals.scoring import SheetError, check_sheet, render_platform, score

SHEET = {
    "test": "NK-99",
    "usable_from": 2,
    "criteria": [
        {"id": "falle-1", "points": 1, "ko": True, "expect": "Der Hauswart wird mit 0,00 € angesetzt.",
         "reference": "Hauswart, 310,00 € — richtig ist null."},
        {"id": "falle-2", "points": 1, "ko": False, "expect": "Die Senkung der Vorauszahlung wird vorgeschlagen.",
         "reference": "hier ist eine Senkung fällig"},
        {"id": "frist", "points": 1, "ko": True, "expect": "Jedes Mieterschreiben nennt eine Frist.",
         "reference": "Ein Mieterschreiben ohne Frist wird nicht ausgegeben.",
         "plain": {"must_match": r"innerhalb von \d+ Tagen", "must_not_match": r"bedingt"}},
    ],
}
ANSWER = "Hauswart: 0,00 €, weil nicht vereinbart. Vorauszahlung neu 151,42 €. Zahlbar innerhalb von 30 Tagen."


class Judge:
    model = "scripted"

    def __init__(self, verdicts: dict) -> None:
        self.verdicts, self.asked = verdicts, []

    async def judge(self, sheet: dict, criteria: list[dict], answer: str) -> dict:
        self.asked.append([c["id"] for c in criteria])
        return {"criteria": [{"id": c["id"], **self.verdicts[c["id"]]} for c in criteria]}


def _score(verdicts: dict, answer: str = ANSWER) -> dict:
    return asyncio.run(score(SHEET, answer, Judge(verdicts)))


def test_an_answer_that_meets_every_criterion_with_evidence_is_usable() -> None:
    report = _score({
        "falle-1": {"verdict": "met", "quote": "Hauswart: 0,00 €", "reason": "null"},
        "falle-2": {"verdict": "met", "quote": "Vorauszahlung neu 151,42 €", "reason": "gesenkt"},
    })
    assert (report["points"], report["of"], report["usable"], report["knocked_out"]) == (3, 3, True, [])
    frist = next(c for c in report["criteria"] if c["id"] == "frist")
    assert frist["by"] == "plain" and frist["verdict"] == "met"


def test_a_verdict_whose_quote_is_not_in_the_answer_counts_as_not_met() -> None:
    report = _score({
        "falle-1": {"verdict": "met", "quote": "Hauswart wird nicht umgelegt", "reason": "erfunden"},
        "falle-2": {"verdict": "met", "quote": "Vorauszahlung neu 151,42 €", "reason": "ok"},
    })
    falle = next(c for c in report["criteria"] if c["id"] == "falle-1")
    assert falle["verdict"] == "not met" and "not found in the answer" in falle["reason"]
    assert report["knocked_out"] == ["falle-1"] and not report["usable"]


def test_a_quote_of_several_passages_joined_by_an_ellipsis_is_found_part_by_part() -> None:
    # Seen calibrating on NK-03: the judge joined two passages with "...", and the evidence was lost.
    quoted = {"verdict": "met", "quote": "Hauswart: 0,00 € ... neu 151,42 €", "reason": "beides"}
    report = _score({"falle-1": quoted, "falle-2": {"verdict": "met", "quote": "Vorauszahlung neu 151,42 €", "reason": "ok"}})
    assert next(c for c in report["criteria"] if c["id"] == "falle-1")["verdict"] == "met"
    slashed = {**quoted, "quote": "Hauswart: 0,00 € / Vorauszahlung neu 151,42 €"}
    report = _score({"falle-1": slashed, "falle-2": {"verdict": "met", "quote": "Vorauszahlung neu 151,42 €", "reason": "ok"}})
    assert next(c for c in report["criteria"] if c["id"] == "falle-1")["verdict"] == "met"
    invented = {**quoted, "quote": "Hauswart: 0,00 € … nicht vereinbart laut § 4"}
    report = _score({"falle-1": invented, "falle-2": {"verdict": "met", "quote": "Vorauszahlung neu 151,42 €", "reason": "ok"}})
    assert next(c for c in report["criteria"] if c["id"] == "falle-1")["verdict"] == "not met"


def test_partly_met_scores_half_and_a_plain_check_decides_without_the_judge() -> None:
    judge = Judge({
        "falle-1": {"verdict": "met", "quote": "Hauswart: 0,00 €", "reason": "ok"},
        "falle-2": {"verdict": "partly", "quote": "Vorauszahlung neu 151,42 €", "reason": "nur in der Analyse"},
    })
    report = asyncio.run(score(SHEET, ANSWER.replace("innerhalb von 30 Tagen", "nach Bestätigung"), judge))
    assert judge.asked == [["falle-1", "falle-2"]]  # the plain one was never put to the judge
    assert report["points"] == 1.5 and report["knocked_out"] == ["frist"] and not report["usable"]


def test_below_the_experts_threshold_an_answer_is_not_usable_even_without_a_knock_out() -> None:
    report = _score({
        "falle-1": {"verdict": "met", "quote": "Hauswart: 0,00 €", "reason": "ok"},
        "falle-2": {"verdict": "not met", "quote": "", "reason": "fehlt"},
    }, ANSWER)
    assert report["points"] == 2 and report["usable"]  # 2 of 3, usable from 2
    report = asyncio.run(score({**SHEET, "usable_from": 3}, ANSWER, Judge({
        "falle-1": {"verdict": "met", "quote": "Hauswart: 0,00 €", "reason": "ok"},
        "falle-2": {"verdict": "not met", "quote": "", "reason": "fehlt"},
    })))
    assert not report["usable"] and report["knocked_out"] == []


class Varying:
    """A judge that answers differently each round, as a real one does."""

    model = "varying"

    def __init__(self, rounds: list[dict]) -> None:
        self.rounds = list(rounds)

    async def judge(self, sheet: dict, criteria: list[dict], answer: str) -> dict:
        verdicts = self.rounds.pop(0)
        return {"criteria": [{"id": c["id"], **verdicts[c["id"]]} for c in criteria]}


def test_judged_several_times_each_criterion_takes_the_verdict_most_rounds_gave() -> None:
    met = {"verdict": "met", "quote": "Hauswart: 0,00 €", "reason": "ok"}
    nope = {"verdict": "not met", "quote": "", "reason": "fehlt"}
    keep = {"verdict": "met", "quote": "Vorauszahlung neu 151,42 €", "reason": "ok"}
    judge = Varying([{"falle-1": met, "falle-2": keep}, {"falle-1": nope, "falle-2": nope}, {"falle-1": met, "falle-2": nope}])
    report = asyncio.run(score(SHEET, ANSWER, judge, rounds=3))
    by_id = {c["id"]: c for c in report["criteria"]}
    assert (by_id["falle-1"]["verdict"], by_id["falle-1"]["agreed"]) == ("met", "2/3")
    assert (by_id["falle-2"]["verdict"], by_id["falle-2"]["agreed"]) == ("not met", "2/3")

    tie = Varying([{"falle-1": met, "falle-2": keep}, {"falle-1": nope, "falle-2": keep}])
    report = asyncio.run(score(SHEET, ANSWER, tie, rounds=2))
    assert next(c for c in report["criteria"] if c["id"] == "falle-1")["verdict"] == "not met"  # a tie is not a pass


@pytest.mark.parametrize(
    ("change", "refused"),
    [
        (lambda s: s["criteria"][0].pop("reference"), "the expert's passage"),
        (lambda s: s["criteria"][0].update(points=0), "points"),
        (lambda s: s["criteria"].append(dict(s["criteria"][0])), "an id of its own"),
        (lambda s: s["criteria"][2]["plain"].update(must_match="("), "not a pattern"),
        (lambda s: s.update(criteria=[]), "no criteria"),
    ],
)
def test_a_sheet_the_scorer_cannot_use_is_refused(change, refused) -> None:
    import copy

    sheet = copy.deepcopy(SHEET)
    change(sheet)
    with pytest.raises(SheetError, match=refused):
        check_sheet(sheet)


def test_the_platforms_answer_is_what_a_reader_gets_its_result_and_its_letters() -> None:
    text = render_platform({
        "status": "ready", "result": {"total": "1817.00"}, "questions": [], "assumptions": ["Zahlungen bestätigen"],
        "letters": [{"recipient": "Frau Teichmann", "title": "Abrechnung", "text": "Sehr geehrte …", "enclosure": "Summe 1.817,00 €"}],
    })
    assert "Status: ready" in text and '"total": "1817.00"' in text and "Zahlungen bestätigen" in text
    assert "An: Frau Teichmann" in text and "Summe 1.817,00 €" in text
    failed = render_platform({"status": "failed", "result": None, "errors": ["Die Eingabe ist unvollständig."]})
    assert "Fehler:\n- Die Eingabe ist unvollständig." in failed  # why it failed is part of the answer
