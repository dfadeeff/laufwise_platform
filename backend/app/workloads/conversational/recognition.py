"""Which of a practice's services a caller's words could mean (ADR-0026, Mia spec v3 #131).

A pure function of the practice's own data: the service names and the phrases the practice says
callers use for each, in any language. Nothing here knows a treatment; a podiatry practice and an
estate agent fill the same lists.

Ambiguity is data too. A phrase the practice puts on several services ("Fußpflege" on both the
small and the full treatment, "Nagelspange" on the first and the follow-up session) matches all of
them, so the agent is told to ask rather than choose. A more specific phrase wins over one it
contains: "große Fußpflege" is the full treatment alone, not both.

Matching is deliberately forgiving about grammar, never about meaning: words are compared without
case or accents, a long word matches by its stem so "Warzen" and "бородавку" still count, and a
phrase matches inside a compound so "Dornwarze" contains "Warze". A match is a recognition signal,
not a diagnosis — it never books anything.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def words(text: str) -> tuple[str, ...]:
    """Lower-case words without accents: "Hühnerauge" → ("huhnerauge",), "ёж" → ("еж",)."""
    plain = unicodedata.normalize("NFKD", text.casefold().replace("ß", "ss"))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return tuple(re.findall(r"\w+", plain))


def _covers(heard: str, word: str) -> bool:
    # A short word ("na", "ab") must be said as it is, or it would match inside everything.
    if len(word) <= 3:
        return heard == word
    return word[: max(4, len(word) - 2)] in heard


def _matches(said: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    return bool(phrase) and all(any(_covers(h, w) for h in said) for w in phrase)


def candidates(services: Iterable[tuple[str, Iterable[str]]], said: str) -> list[str]:
    """The keys of the services the caller's words could mean, in the practice's order.

    `services` is (key, phrases) per service, its name among the phrases. Empty: nothing matched.
    """
    heard = words(said)
    best: dict[str, frozenset[str]] = {}
    for key, phrases in services:
        for phrase in phrases:
            tokens = words(phrase)
            if _matches(heard, tokens) and len(tokens) > len(best.get(key, ())):
                best[key] = frozenset(tokens)
    # "große Fußpflege" matched the full treatment; "Fußpflege" alone matched both. The full one
    # was named more precisely, so the small one is not a candidate.
    return [
        key for key, tokens in best.items() if not any(tokens < other for other in best.values())
    ]
