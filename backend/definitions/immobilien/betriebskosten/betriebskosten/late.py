"""A settlement made after its deadline, § 556 Abs. 3 BGB, computed from the case and its
assessment (ADR-0042). The model says, per position, whether the landlord is responsible for its
being late and from when he could first have billed it, quoting its sources; this module does the
arithmetic and the dates, and the check (`verify.py`) re-derives them on its own.

    deadline     § 556 Abs. 3 Satz 2: twelve months after the period ends; after it, a claim for
                 more than the advances is excluded (Satz 3) ...
    exempt       ... unless the landlord is not responsible for the delay (Satz 3, second half):
                 a position he could bill only later stays claimable (BGH VIII ZR 264/12: he may
                 reserve it), as a rule for three months from when the obstacle fell away (BGH,
                 Urt. v. 5.7.2006 – VIII ZR 220/05, its headnote; not 264/12, which says nothing of
                 a period). The model says what the obstacle was, a fact; whether it excuses is
                 law, applied here: a late statement of the owners' association never does (BGH,
                 Urt. v. 25.1.2017 – VIII ZR 249/15, headnotes a and b), whatever the model judged
    the balance  the advances are set against the excluded positions first: what they leave
                 over is lost; a credit is always refunded (the exclusion cuts off the landlord's
                 claims, not the tenant's), and the exempt positions are claimed on top, or set
                 against that credit
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from betriebskosten.model import VACANCY, Case, Claim, LateResult, Recommendation, Statement
from shared.letters import euro as _de

MONTHS_TO_CLAIM = 3
LATER_CLAIM = "BGH, Urt. v. 5.7.2006 – VIII ZR 220/05"
NO_EXCUSE = {"weg_abrechnung": "BGH, Urt. v. 25.1.2017 – VIII ZR 249/15"}  # obstacles the law says do not excuse


def excused(p) -> bool:
    """The landlord is not responsible for this position's being late, as assessed and as the law allows."""
    return not p.responsible and p.could_bill_from is not None and p.obstacle not in NO_EXCUSE


def months_after(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    first = date(d.year + y, m + 1, 1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return first.replace(day=min(d.day, last.day))


def late(case: Case, statements: list[Statement], deadline: date, questions: list[str]):
    """The statements with what stays claimable, and what the late settlement comes to; or the
    statements unchanged and None, when the settlement is not late."""
    if case.today is None or case.today <= deadline:
        return statements, None
    if case.late is None:
        questions.append(
            f"Die Abrechnungsfrist nach § 556 Abs. 3 BGB lief am {deadline:%d.%m.%Y} ab; die Einschätzung sagt "
            "nicht, welche Positionen der Vermieter zu vertreten hat."
        )
        return statements, None
    assessed = {p.position_id: p for p in case.late.positions}
    charged = {line.position_id for s in statements for line in s.lines if line.allocable > 0}
    if missing := sorted(charged - set(assessed)):
        questions.append(f"Die Einschätzung sagt nicht, ob der Vermieter die Verspätung zu vertreten hat: {', '.join(missing)}.")
        return statements, None

    claims: dict[str, Claim] = {}
    for p in case.positions:
        a = assessed.get(p.id)
        if a and excused(a) and p.id in charged:
            claim_by = months_after(a.could_bill_from, MONTHS_TO_CLAIM)
            if case.today <= claim_by:
                allocable = next(line.allocable for s in statements for line in s.lines if line.position_id == p.id)
                claims[p.id] = Claim(p.id, p.label, allocable, a.could_bill_from, claim_by)

    out = []
    for s in statements:
        if s.party == VACANCY or s.balance is None:
            out.append(s)
            continue
        lines = tuple(replace(line, claimable=line.share if line.position_id in claims else Decimal("0.00")) for line in s.lines)
        exempt = sum((line.claimable for line in lines), Decimal("0.00"))
        excluded = s.total - exempt - s.advances
        out.append(replace(
            s, lines=lines, claimable=exempt, lost=max(excluded, Decimal("0.00")),
            payable=exempt + min(excluded, Decimal("0.00")),
        ))

    lost = sum((s.lost or Decimal("0.00") for s in out), Decimal("0.00"))
    recommendations = [Recommendation(
        1, case.today,
        "Die Abrechnung jetzt versenden: die abgelaufene Frist schließt Nachforderungen aus, nicht die "
        "Pflicht abzurechnen, und ein Guthaben ist auszuzahlen.",
    )]
    for claim_by in sorted({c.claim_by for c in claims.values()}):
        named = ", ".join(c.label for c in claims.values() if c.claim_by == claim_by)
        recommendations.append(Recommendation(
            1, claim_by, f"{named} geltend machen: spätestens bis {claim_by:%d.%m.%Y}, drei Monate nach dem "
            f"Wegfall des Hindernisses ({LATER_CLAIM}).",
        ))
    for_a_person = []
    if lost and case.late.damages_claim:
        question = (f"Zur Entscheidung: Ob für die verlorene Nachforderung von {_de(lost)} Schadensersatz gegen die "
                    "WEG-Verwaltung geltend gemacht wird.")
        for_a_person.append(question)
        recommendations.append(Recommendation(2, case.today + timedelta(days=30), question))
    if case.late.lesson:
        recommendations.append(Recommendation(3, months_after(deadline, 12), case.late.lesson))
    return out, LateResult(
        deadline=deadline, today=case.today, claims=tuple(claims.values()),
        recommendations=tuple(recommendations), lesson=case.late.lesson, for_a_person=tuple(for_a_person),
    )
