"""NK-02 through the platform's run (ADR-0039 step 3b): the July boundary is held as a wait.

What the expert's review asks for (Lessons Learned NK 01/02, § 6): one fact is missing, from whom,
by when, and the rest stands. The platform now holds the case for that fact: the settled letters
go out, and once the Messdienst's figure is entered the run computes the settlement again and
offers only what was not yet sent. The figure used here is made up; it is not the expert's.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.db.models import WorkflowAgent, WorkflowCase
from app.kernel.modules import workflow
from app.kernel.workflows import with_value
from app.workflow_agents.runs import awaited, resume_run, run_case
from test_betriebskosten_gold import SETTINGS, _raw

MESSDIENST_SHARE = "0.30"  # hypothetical: the Messdienst has not answered in the test case


def _nk02() -> tuple[WorkflowCase, WorkflowAgent]:
    tenant = uuid.uuid4()
    case = WorkflowCase(id=uuid.uuid4(), tenant_id=tenant, agent_id=uuid.uuid4(), title="NK-02",
                        input=_raw("nk02"), input_revision=1, errors=[], questions=[],
                        assumptions=[], problems=[], drafts=[])
    return case, WorkflowAgent(id=case.agent_id, tenant_id=tenant, settings=SETTINGS)


def test_nk02_waits_for_the_messdienst_and_what_stands_goes_out_meanwhile() -> None:
    case, agent = _nk02()
    run = run_case(case, agent, workflow("betriebskosten")[1])

    assert (run.state, case.status) == ("suspended", "waiting")
    (wait,) = awaited(run)
    assert (wait["fact"], wait["from"], wait["due"]) == ("heating.hot_water_share", "Messdienst", "2026-12-01")
    assert "01.07.2025–31.12.2025" in wait["what"]
    # Herr Özdemir's settlement is final; Frau Lindqvist hears what is open and by when.
    assert [d["recipient"] for d in case.drafts] == ["Herr Özdemir", "Frau Lindqvist", "Messdienst"]
    assert case.drafts[1]["title"].endswith("Zwischenstand")


def test_the_messdienst_figure_completes_it_and_no_letter_goes_out_twice() -> None:
    case, agent = _nk02()
    betriebskosten = workflow("betriebskosten")[1]
    run = run_case(case, agent, betriebskosten)
    heating_before = _heating(case.result) + sum(Decimal(o["amount"]) for o in case.result["open_items"])
    sent = list(case.drafts)  # all three were sent while it waited
    oezdemir = sent[0]

    case.input = with_value(case.input, "heating.hot_water_share", MESSDIENST_SHARE)
    case.input_revision += 1
    resume_run(case, agent, betriebskosten, run, sent)

    assert (run.state, run.outcome, case.status) == ("succeeded", "ready", "ready")
    statements = {s["party"]: s for s in case.result["statements"]}
    assert all(s["final"] for s in statements.values()) and case.result["open_items"] == []
    # The same Özdemir letter as before: already sent, so not offered again.
    assert [d["recipient"] for d in case.drafts] == ["Frau Lindqvist"]
    assert not case.drafts[0]["title"].endswith("Zwischenstand")
    assert oezdemir["text"] not in [d["text"] for d in case.drafts]
    # The open pool is split now: the heating position is on the statements, to the cent.
    assert _heating(case.result) == heating_before


def _heating(result: dict) -> Decimal:
    return sum(
        (Decimal(line["share"]) for s in result["statements"] for line in s["lines"] if line["position_id"] == "U1-02"),
        Decimal("0.00"),
    )
