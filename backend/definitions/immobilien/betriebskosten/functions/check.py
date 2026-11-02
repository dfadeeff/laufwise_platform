"""Check: the postcondition of a computed result (ADR-0039 D3). The platform calls it in its own
sandbox call, so the computation cannot reach it."""

from betriebskosten.workflow import check_step


def run(input: dict, settings: dict, rules: dict) -> list[str]:
    return check_step(input["case"], settings, rules, input["result"])
