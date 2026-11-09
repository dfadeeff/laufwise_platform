"""A rent increase up to the local rent, § 558 BGB, computed from the case's facts and the cap its
assessment found (ADR-0042). The model says which cap applies, quoting its source; this module does
the arithmetic and the dates, and the check (`verify.py`) re-derives them on its own.

    effective    § 558b Abs. 1: the start of the third calendar month after the request reached
                 the tenant
    consent_by   § 558b Abs. 2: the end of the second calendar month after it reached the tenant
    base         § 558 Abs. 3: the rent three years before it takes effect; increases under
                 §§ 559-560 are left out of the cap (and out of the waiting period, Abs. 1), and
                 a surcharge in force stays on top of the new rent
    the limit    the lower of the cap and the local rent (area × the Mietspiegel's figure)
    waiting      § 558 Abs. 1: the rent unchanged for 15 months when it takes effect, and the
                 request at least a year after the last increase
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from shared.letters import euro as _de
from betriebskosten.model import RentIncrease, RentIncreaseResult

_CENT = Decimal("0.01")


def first_of_month_after(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def end_of_month_after(d: date, months: int) -> date:
    first = first_of_month_after(d, months)
    return first.replace(day=calendar.monthrange(first.year, first.month)[1])


def _years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month - (1 if end.day < start.day else 0)


def rent_increase(r: RentIncrease, questions: list[str]) -> RentIncreaseResult | None:
    if r.cap_percent is None:
        questions.append("Die Einschätzung nennt die Kappungsgrenze nach § 558 Abs. 3 BGB nicht.")
        return None
    effective = first_of_month_after(r.delivered, 3)
    consent_by = end_of_month_after(r.delivered, 2)
    base_date = _years_before(effective, 3)
    then = [s for s in r.rents if s.start <= base_date]
    now = [s for s in r.rents if s.start <= effective]
    if not then or not now:
        questions.append(
            f"Die Miete vor drei Jahren (zum {base_date:%d.%m.%Y}) ist aus den Unterlagen nicht ersichtlich."
        )
        return None
    base, current = then[-1], now[-1]
    unchanged_since = max(s.start for s in now if s.basis != "559")
    months = _months_between(unchanged_since, effective)
    if months < 15 or _years_before(r.delivered, 1) < unchanged_since:
        questions.append(
            f"Die Miete ist erst seit {unchanged_since:%d.%m.%Y} unverändert: § 558 Abs. 1 BGB verlangt bei "
            f"Wirksamwerden 15 Monate und ein Jahr seit der letzten Erhöhung bis zum Verlangen."
        )
        return None
    cap_limit = (base.rent * (1 + r.cap_percent / 100)).quantize(_CENT, ROUND_HALF_UP)
    local_rent = (r.area_sqm * r.local_rent_per_sqm).quantize(_CENT, ROUND_HALF_UP)
    new_rent = min(cap_limit, local_rent)
    binding = "Kappungsgrenze" if cap_limit <= local_rent else "ortsübliche Vergleichsmiete"
    if new_rent <= current.rent:
        questions.append(
            f"Die zulässige Miete ({_de(new_rent)}) liegt nicht über der bisherigen ({_de(current.rent)}): "
            "eine Erhöhung nach § 558 BGB ist nicht möglich."
        )
        return None
    surcharge = current.surcharge
    percent = f"{r.cap_percent.normalize():f}".replace(".", ",")
    return RentIncreaseResult(
        tenant=r.tenant,
        delivered=r.delivered,
        consent_by=consent_by,
        effective=effective,
        base_date=base_date,
        base_rent=base.rent,
        cap_percent=r.cap_percent,
        cap_limit=cap_limit,
        local_rent=local_rent,
        current_rent=current.rent,
        surcharge=surcharge,
        current_total=current.rent + surcharge,
        new_rent=new_rent,
        new_total=new_rent + surcharge,
        increase=new_rent - current.rent,
        binding=binding,
        unchanged_since=unchanged_since,
        months_unchanged=months,
        formal=(
            "Textform: Das Verlangen ergeht in Textform (§ 558a Abs. 1 BGB).",
            f"Begründung: Mietspiegel, {_de(r.local_rent_per_sqm)} je m² (§ 558a Abs. 2 Nr. 1 BGB).",
            f"Sperrfrist: Die Miete ist seit {unchanged_since:%d.%m.%Y} unverändert, bei Wirksamwerden "
            f"{months} Monate (§ 558 Abs. 1 BGB); ein Zuschlag nach § 559 BGB bleibt dabei außer Betracht.",
        ),
        reasoning=(
            f"Ortsübliche Vergleichsmiete: {r.area_sqm.normalize():f} m² × {_de(r.local_rent_per_sqm)} = {_de(local_rent)}. "
            f"Kappungsgrenze ({percent} %, § 558 Abs. 3 BGB): Miete drei Jahre vor Wirksamwerden ({base_date:%d.%m.%Y}) "
            f"{_de(base.rent)} × (1 + {percent} %) = {_de(cap_limit)}. Bindend ist die {binding}: höchstens "
            f"{_de(new_rent)} statt bisher {_de(current.rent)}, also {_de(new_rent - current.rent)} mehr"
            + (f"; dazu der fortbestehende Zuschlag nach § 559 BGB von {_de(surcharge)}, zusammen {_de(new_rent + surcharge)}."
               if surcharge else ".")
        ),
    )
