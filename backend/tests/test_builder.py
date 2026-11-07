"""The builder (ADR-0039 D6, step 6): a model writes a draft; it never publishes.

The model here is scripted: each test says what it answers, and sees what it was shown. What is
proved is the governance around any model: what it may change, that every figure it writes is
unverified until a person confirms it, that it is shown the failures and told to fix code rather
than figures, and that nothing it does publishes.
"""

from __future__ import annotations

import copy
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import Principal, current_principal, current_tenant
from app.builder import service as builder
from app.builder.apply import ProposalError, apply
from app.builder.model import BuilderUnavailable
from app.db.models import BuilderRun
from app.main import app
from tests.test_definition_versions import DEMO, _in_db, seeded  # noqa: F401
from tests.test_workflow_agents import _as, db

client = TestClient(app)


def _demo() -> dict:
    from app.kernel.definitions import read_definition

    return copy.deepcopy(read_definition(DEMO / "umlage", DEMO))


# --- applying an answer -------------------------------------------------------------------


def test_every_figure_the_builder_writes_is_unverified_and_one_it_leaves_keeps_its_confirmation() -> None:
    content = _demo()
    u2 = next(c for c in content["tests"] if c["name"] == "u2")
    u2["expect"]["result"]["total"]["confirmed_by"] = "user_ada"
    proposed = copy.deepcopy(u2)
    proposed["expect"]["result"]["total"] = {k: v for k, v in u2["expect"]["result"]["total"].items() if k != "confirmed_by"}
    proposed["expect"]["result"]["statements[party=Frau Berg].balance"]["value"] = "-196.40"
    proposed["expect"]["result"]["statements[party=Herr Adler].total"] = {"value": "1048.47", "ref": "Rechnung 1", "unverified": False}

    out, changed = apply(content, {"cases": [proposed]})
    figures = next(c for c in out["tests"] if c["name"] == "u2")["expect"]["result"]

    assert figures["total"] == {**u2["expect"]["result"]["total"]}  # untouched: still confirmed
    assert figures["statements[party=Frau Berg].balance"]["unverified"] is True  # changed
    assert figures["statements[party=Herr Adler].total"]["unverified"] is True  # new, whatever it said
    assert changed == ["changed case u2"]


@pytest.mark.parametrize(
    ("proposal", "refused"),
    [
        ({"set": {"key": "other"}}, "only label"),
        ({"set": {"version": 9}}, "only label"),
        ({"files": {"shared/letters.py": "x = 1"}}, "not shared/"),
        ({"files": {"functions/compute.txt": "x"}}, "a .py file"),
        ({"files": {"functions/compute.py": 42}}, "whole text"),
        ({"publish": True}, "answer with one object"),
        ({"cases": [{"expect": {}}]}, "with a name"),
        ("just text", "answer with one object"),
    ],
)
def test_the_builder_changes_only_a_definitions_own_parts(proposal, refused) -> None:
    with pytest.raises(ProposalError, match=refused):
        apply(_demo(), proposal)


def test_files_are_written_whole_or_deleted() -> None:
    out, changed = apply(_demo(), {"files": {"umlage/extra.py": "X = 1\n", "functions/letters.py": None}})
    assert out["sources"]["umlage/extra.py"] == "X = 1\n" and "functions/letters.py" not in out["sources"]
    assert changed == ["wrote umlage/extra.py", "deleted functions/letters.py"]


@pytest.mark.parametrize(
    "text",
    [
        "from umlage import calculate as _calc\n\n_calc._split = lambda *a: 0\n",
        "import umlage.calculate\n\numlage.calculate.RATE = 2\n",
        "from umlage import calculate\n\nsetattr(calculate, '_split', None)\n",
    ],
)
def test_the_builder_fixes_a_cause_where_it_is_never_by_patching_another_module(text) -> None:
    # Seen with the real model (step 6c): it copied a broken function into compute.py and swapped
    # it in over the original. The cases passed; the bug stayed where it was, now twice.
    with pytest.raises(ProposalError, match="fix it in the file that defines it"):
        apply(_demo(), {"files": {"functions/compute.py": text}})


def test_code_that_does_not_parse_is_refused_before_it_runs() -> None:
    with pytest.raises(ProposalError, match="not Python"):
        apply(_demo(), {"files": {"functions/compute.py": "def run(:\n"}})


def test_a_module_may_still_set_its_own_names() -> None:
    text = "from decimal import Decimal\n\nRATE = Decimal('0.5')\n\nclass A:\n    def f(self):\n        self.x = 1\n"
    out, _ = apply(_demo(), {"files": {"umlage/extra.py": text}})
    assert out["sources"]["umlage/extra.py"] == text


# --- a run, against the database, with a scripted model --------------------------------------


class Scripted:
    """Answers in turn, and keeps every turn it was shown."""

    model = "scripted"

    def __init__(self, *answers) -> None:
        self.answers, self.seen = list(answers), []

    async def answer(self, system: str, turns: list[dict]) -> dict:
        self.seen.append((system, copy.deepcopy([{k: v for k, v in t.items() if k != "documents"} for t in turns])))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def workspace(seeded, monkeypatch):  # noqa: F811
    org = f"org_test_builder_{uuid.uuid4().hex[:8]}"
    app.dependency_overrides[current_tenant] = _as(org, "demo")
    app.dependency_overrides[current_principal] = lambda: Principal("user_ada", "org:admin")
    scripted: dict = {}
    monkeypatch.setattr(builder, "builder_model", lambda: scripted["model"])
    yield scripted
    app.dependency_overrides.pop(current_tenant, None)
    app.dependency_overrides.pop(current_principal, None)


def _mine() -> dict:
    key = client.post("/api/v1/definitions", json={"template": "umlage", "label": "Umlage"}).json()["key"]
    return client.get(f"/api/v1/definitions/{key}").json()


def _ask(key: str, instruction: str = "Schreibe die Umlage neu."):
    return client.post(f"/api/v1/definitions/{key}/builder", json={"instruction": instruction})


@db
def test_a_run_writes_the_draft_and_publishing_waits_for_a_person_to_confirm_its_figures(workspace) -> None:
    mine = _mine()
    u2 = copy.deepcopy(next(c for c in mine["draft"]["content"]["tests"] if c["name"] == "u2"))
    u2["expect"]["result"]["statements[party=Herr Adler].total"] = {"value": "1048.47", "ref": "Rechnung 1"}
    workspace["model"] = model = Scripted({"summary": "Verteilt Kosten je Partei.", "set": {"label": "Umlage (neu)"}, "cases": [u2]})

    asked = _ask(mine["key"])
    assert asked.status_code == 202 and asked.json()["state"] == "queued"
    (run,) = client.get(f"/api/v1/definitions/{mine['key']}/builder").json()
    assert (run["state"], run["model"], run["draft_version"]) == ("succeeded", "scripted", 1)
    assert run["summary"] == "Verteilt Kosten je Partei."
    assert run["rounds"] == [{
        "round": 1, "applied": True, "changed": ["label", "changed case u2"], "passed": True, "failures": [],
        "unverified": ["u2: statements[party=Herr Adler].total"],
    }]
    # It was shown the instruction and the definition as it is, never its key or version.
    system, turns = model.seen[0]
    assert "You never publish" in system and "Schreibe die Umlage neu." in turns[0]["text"]
    assert '"key"' not in turns[0]["text"].split("as JSON:\n", 1)[1][:20]

    detail = client.get(f"/api/v1/definitions/{mine['key']}").json()
    assert detail["published"] is None and detail["draft"]["content"]["label"] == "Umlage (neu)"
    refused = client.post(f"/api/v1/definitions/{mine['key']}/publish")
    assert refused.status_code == 422 and "not yet confirmed" in refused.json()["detail"]

    confirm = {"case": "u2", "path": "statements[party=Herr Adler].total"}
    assert client.post(f"/api/v1/definitions/{mine['key']}/draft/confirm", json=confirm).status_code == 200
    assert client.post(f"/api/v1/definitions/{mine['key']}/draft/confirm", json=confirm).status_code == 409
    done = client.post(f"/api/v1/definitions/{mine['key']}/publish")
    assert done.status_code == 200 and done.json()["version"] == 1
    figure = next(c for c in client.get(f"/api/v1/definitions/{mine['key']}").json()["published"]["content"]["tests"]
                  if c["name"] == "u2")["expect"]["result"]["statements[party=Herr Adler].total"]
    assert figure == {"value": "1048.47", "ref": "Rechnung 1", "confirmed_by": "user_ada"}


@db
def test_a_failing_case_is_shown_to_the_builder_which_must_fix_the_code_not_the_figure(workspace) -> None:
    mine = _mine()
    original = mine["draft"]["content"]["sources"]["functions/compute.py"]
    broken = "def run(input, settings, rules):\n    raise ValueError('nicht fertig')\n"
    workspace["model"] = model = Scripted(
        {"summary": "Erster Versuch.", "files": {"functions/compute.py": broken}},
        {"summary": "Repariert.", "files": {"functions/compute.py": original}},
    )
    _ask(mine["key"])
    (run,) = client.get(f"/api/v1/definitions/{mine['key']}/builder").json()

    assert run["state"] == "succeeded" and [r["passed"] for r in run["rounds"]] == [False, True]
    assert any("nicht fertig" in f for f in run["rounds"][0]["failures"])
    feedback = model.seen[1][1][-1]["text"]
    assert "These failed" in feedback and "Do not change an expected figure" in feedback
    assert run["summary"] == "Repariert."


@db
def test_after_four_rounds_the_last_draft_is_handed_over_with_its_failures(workspace) -> None:
    mine = _mine()
    broken = {"files": {"functions/compute.py": "def run(input, settings, rules):\n    raise ValueError('x')\n"}}
    workspace["model"] = Scripted(*[{"summary": f"Versuch {i}", **broken} for i in range(1, 6)])
    _ask(mine["key"])
    (run,) = client.get(f"/api/v1/definitions/{mine['key']}/builder").json()
    assert run["state"] == "succeeded" and len(run["rounds"]) == builder.MAX_ROUNDS
    assert not any(r["passed"] for r in run["rounds"])
    report = client.post(f"/api/v1/definitions/{mine['key']}/draft/test").json()
    assert not report["passed"]  # the person sees the same failures, and nothing is published
    assert client.get(f"/api/v1/definitions/{mine['key']}").json()["published"] is None


@db
def test_an_answer_that_cannot_be_applied_is_refused_and_the_draft_stays_as_it_was(workspace) -> None:
    mine = _mine()
    workspace["model"] = model = Scripted(*[{"set": {"key": "x"}}] * builder.MAX_ROUNDS)
    _ask(mine["key"])
    (run,) = client.get(f"/api/v1/definitions/{mine['key']}/builder").json()

    assert run["state"] == "failed" and run["draft_version"] is None
    assert all(not r["applied"] for r in run["rounds"]) and "not applied" in model.seen[1][1][-1]["text"]
    assert client.get(f"/api/v1/definitions/{mine['key']}").json()["draft"]["content"] == mine["draft"]["content"]


@db
def test_a_model_that_does_not_answer_fails_the_run_and_says_why(workspace) -> None:
    mine = _mine()
    workspace["model"] = Scripted(BuilderUnavailable("the builder needs OPENAI_API_KEY"))
    _ask(mine["key"])
    (run,) = client.get(f"/api/v1/definitions/{mine['key']}/builder").json()
    assert (run["state"], run["error"]) == ("failed", "the builder needs OPENAI_API_KEY")


@db
def test_a_new_judgment_shows_on_the_guardrail_panel_as_a_change(workspace) -> None:
    mine = _mine()
    steps = [s for s in mine["draft"]["content"]["procedure"] if s["kind"] != "decide"]
    # The case that expects a decision goes with the decide step, or it would fail.
    workspace["model"] = Scripted({"summary": "Keine strittigen Posten mehr.", "set": {"procedure": steps}, "remove_cases": ["u4"]})
    _ask(mine["key"])
    (run,) = client.get(f"/api/v1/definitions/{mine['key']}/builder").json()
    changed = {c["question"]: c for c in run["guardrail_changes"]}
    assert changed["Which judgments are a person's?"]["after"] == "None in this agent."


@db
def test_the_builder_works_only_on_the_workspaces_own_drafts_one_run_at_a_time(workspace) -> None:
    workspace["model"] = Scripted()
    assert _ask("umlage").status_code == 403  # a shipped template
    mine = _mine()

    async def queue(session):
        from app.db import repo

        definition = await repo.get_definition(session, mine["key"])
        session.add(BuilderRun(tenant_id=definition.tenant_id, definition_id=definition.id, requested_by="x",
                               instruction="y", file_ids=[], state="running", rounds=[], guardrail_changes=[]))
        await session.commit()

    _in_db(queue)
    assert _ask(mine["key"]).status_code == 409

    from app.db.session import get_sessionmaker

    assert _in_db(lambda _s: builder.interrupt_stale(get_sessionmaker())) >= 1
    (run,) = client.get(f"/api/v1/definitions/{mine['key']}/builder").json()
    assert run["state"] == "interrupted"

    app.dependency_overrides[current_tenant] = _as(f"org_test_builder_other_{uuid.uuid4().hex[:6]}", "demo")
    assert _ask(mine["key"]).status_code == 404


@db
def test_while_the_builder_works_a_person_cannot_change_the_draft_it_will_overwrite(workspace) -> None:
    mine = _mine()
    key = mine["key"]

    async def running(session):
        from app.db import repo

        definition = await repo.get_definition(session, key)
        session.add(BuilderRun(tenant_id=definition.tenant_id, definition_id=definition.id, requested_by="x",
                               instruction="y", file_ids=[], state="running", rounds=[], guardrail_changes=[]))
        await session.commit()

    _in_db(running)
    content = mine["draft"]["content"]
    assert client.put(f"/api/v1/definitions/{key}/draft", json={"content": content}).status_code == 409
    confirm = {"case": "u2", "path": "x"}
    assert client.post(f"/api/v1/definitions/{key}/draft/confirm", json=confirm).status_code == 409
    assert client.post(f"/api/v1/definitions/{key}/publish").status_code == 409
    assert client.delete(f"/api/v1/definitions/{key}/draft").status_code == 409
    assert client.post(f"/api/v1/definitions/{key}/draft/test").status_code == 200  # reading is fine

    from app.db.session import get_sessionmaker

    _in_db(lambda _s: builder.interrupt_stale(get_sessionmaker()))
    assert client.put(f"/api/v1/definitions/{key}/draft", json={"content": content}).status_code == 200


def test_the_openai_builder_sends_the_guide_the_turns_and_the_documents_and_reads_json() -> None:
    import asyncio
    import json as _json

    import httpx

    from app.builder.model import Document, OpenAIBuilder

    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(_json.loads(request.content))
        return httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": '{"summary": "ok"}'}]}]})

    model = OpenAIBuilder("sk-test", "gpt-test", transport=httpx.MockTransport(handler))
    turns = [{"role": "user", "text": "Baue es.", "documents": [Document("auftrag.txt", "text/plain", b"NK-03")]},
             {"role": "assistant", "text": "{}"}, {"role": "user", "text": "Nochmal."}]
    assert asyncio.run(model.answer("GUIDE", turns)) == {"summary": "ok"}

    assert sent["model"] == "gpt-test" and sent["text"] == {"format": {"type": "json_object"}}
    system, first, reply, again = sent["input"]
    assert system["content"][0]["text"] == "GUIDE"
    assert [p["text"] for p in first["content"]] == ["Baue es.", "Document 1: auftrag.txt", "NK-03"]
    assert reply["content"][0]["type"] == "output_text" and again["content"][0]["text"] == "Nochmal."


def test_the_openai_builder_reports_a_refusal_as_unavailable() -> None:
    import asyncio

    import httpx

    from app.builder.model import OpenAIBuilder

    model = OpenAIBuilder("sk-test", "gpt-test", transport=httpx.MockTransport(lambda r: httpx.Response(429, text="slow down")))
    with pytest.raises(BuilderUnavailable, match="429"):
        asyncio.run(model.answer("GUIDE", [{"role": "user", "text": "x"}]))
