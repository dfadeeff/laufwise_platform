"""Adversarial variants (ADR-0042): a task's text with one fact changed, held to plain checks.
The model runs are not tested here; what must be right without one is."""

from __future__ import annotations

import pytest

from scripts.adversarial import VariantError, failures, varied

TASK = "Kaltmiete: 740,00 €\nVorauszahlung: 180,00 € monatlich\nUnterlage 2 — Grundsteuer 186,40 €\n"

OUTCOME = {
    "status": "ready",
    "result": {"statements": [{"party": "Herr Vogel", "total": "1838.00", "payable": "96.00"},
                              {"party": "Frau Abedi", "total": "2249.00", "payable": "-31.00"}],
               "late": {"claims": []}},
    "questions": ["Die Grundsteuer fehlt."],
    "letters": [{"text": "Bitte zahlen Sie 96,00 €.", "enclosure": "Summe 1.838,00 €"}],
}


def test_an_edit_must_stand_in_the_task_exactly_once() -> None:
    assert varied(TASK, [{"remove": "Unterlage 2 — Grundsteuer 186,40 €\n"}]) == TASK.split("Unterlage")[0]
    assert "190,00" in varied(TASK, [{"replace": ["180,00", "190,00"]}])
    with pytest.raises(VariantError, match="0 times"):  # the task's text drifted: no silent no-op
        varied(TASK, [{"remove": "Unterlage 3"}])
    with pytest.raises(VariantError, match="2 times"):
        varied(TASK + TASK, [{"replace": ["180,00", "190,00"]}])


def test_a_path_picks_a_list_item_by_a_word_it_mentions() -> None:
    assert failures(OUTCOME, [{"path": "result.statements.Abedi.payable", "equals": "-31.00"}]) == []
    assert failures(OUTCOME, [{"path": "result.statements.1.total", "equals": "2249.00"}]) == []
    assert failures(OUTCOME, [{"path": "result.late.claims", "absent": True}]) == []
    (got,) = failures(OUTCOME, [{"path": "result.statements.Vogel.payable", "equals": "0.00"}])
    assert "'96.00'" in got


def test_nowhere_looks_at_the_result_and_the_letters_not_at_the_questions() -> None:
    assert failures(OUTCOME, [{"nowhere": "186.40"}]) == []
    assert failures(OUTCOME, [{"nowhere": "1.838,00"}])  # in the enclosure
    assert failures(OUTCOME, [{"nowhere": "Grundsteuer fehlt"}]) == []  # a question may name it


def test_says_status_and_any() -> None:
    assert failures(OUTCOME, [{"says": "grundsteuer", "in": ["questions"]}]) == []
    assert failures(OUTCOME, [{"says": "Vorauszahlung"}])
    assert failures(OUTCOME, [{"status_not": "ready"}]) and not failures(OUTCOME, [{"status": "ready"}])
    assert failures(OUTCOME, [{"any": [{"status_not": "ready"}, {"says": "Grundsteuer"}]}]) == []
    (got,) = failures(OUTCOME, [{"any": [{"status_not": "ready"}, {"says": "Vorauszahlung"}]}])
    assert got.startswith("none of:")
