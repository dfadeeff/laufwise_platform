"""Letters: the texts written from a checked result (ADR-0034, ADR-0039 D3)."""

from kaution.workflow import letters_step


def run(input: dict, settings: dict, rules: dict) -> list[dict]:
    return letters_step(input["case"], settings, rules, input["result"])
