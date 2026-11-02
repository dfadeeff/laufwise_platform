"""Compute: the raw input to a result, or what to correct (ADR-0039 D3, the function step)."""

from betriebskosten.workflow import compute_step


def run(input: dict, settings: dict, rules: dict) -> dict:
    return compute_step(input, settings, rules)
