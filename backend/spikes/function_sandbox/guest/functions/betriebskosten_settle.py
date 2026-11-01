"""The Betriebskosten settlement as a sandboxed function: compute, then the check, in one call.

It runs the module's own code unchanged (copied into the bundle), with the catalogue handed in as
data instead of read from a file.
"""

from app.modules.immobilien.betriebskosten.calculate import catalogue_from, settle
from app.modules.immobilien.betriebskosten.model import Policy, parse_case
from app.modules.immobilien.betriebskosten.verify import verify
from sdk import plain


def run(input: dict, settings: dict, rules: dict) -> dict:
    catalogue = catalogue_from(rules)
    case = parse_case(input)
    policy = Policy(basis=settings.get("basis") or None)
    settlement = settle(case, catalogue, policy)
    return {"settlement": plain(settlement), "problems": verify(case, settlement, catalogue)}
