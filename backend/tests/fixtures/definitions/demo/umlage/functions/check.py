"""The postcondition: recompute from the input, compare with the result handed in."""

from decimal import Decimal

from umlage.settle import settle


def run(input: dict, settings: dict, rules: dict) -> list[str]:
    expected, *_ = settle(input["case"], rules, settings)
    result = input["result"]
    problems = []
    if result != expected:
        problems.append("the result is not what the input gives")
    total = sum((Decimal(s["total"]) for s in result["statements"]), Decimal("0.00"))
    if total != Decimal(result["total"]):
        problems.append("the statements do not add up to the total")
    return problems
