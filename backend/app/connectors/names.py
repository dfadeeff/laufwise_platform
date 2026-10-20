"""When two written names are the same person's name, and how to search for one (#135, spec §3.2).

A receptionist treats "Anna-Maria Weber" and "anna maria weber" as one name, `Müller` and
`Mueller` as one, and — for a caller from Russia — `Дмитрий Фадеев`, `Dmitry Fadeev` and
`Dmitrij Fadejew` as one. These are the same name *written* differently, never a similar name:
`Anna` and `Anne` stay two people. Only the systematic differences between alphabets and
transliteration schemes are folded, so a birth date that also matches is what binds a card.
"""

from __future__ import annotations

import re

_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}

# Russian (and the Ukrainian/Belarusian letters callers bring) to Latin, the English scheme. The
# German scheme differs only in ways `_translit_key` folds back (w/v, j/y, sch/sh, tsch/ch, …).
_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "yi",
    "є": "ye", "ґ": "g", "ў": "u",
}
# German transliteration of the same letters, used to SEARCH a practice whose cards were typed
# the German way (Фадеев → Fadejew, Жуков → Schukow).
_CYRILLIC_DE = {
    **_CYRILLIC,
    "в": "w", "е": "e", "ё": "jo", "ж": "sch", "з": "s", "й": "j", "х": "ch", "ц": "z",
    "ч": "tsch", "ш": "sch", "щ": "schtsch", "ы": "y", "ю": "ju", "я": "ja", "є": "je", "ї": "ji",
}


def _fold(value: str) -> str:
    """Case, spaces, hyphens and apostrophes are noise; an umlaut is its digraph."""
    folded = (value or "").casefold()
    for umlaut, digraph in _UMLAUTS.items():
        folded = folded.replace(umlaut, digraph)
    return re.sub(r"[\s\-'’]", "", folded)


def has_cyrillic(value: str) -> bool:
    return bool(re.search(r"[Ѐ-ӿ]", value or ""))


_CYRILLIC_VOWELS = set("аеёиоуыэюяіїєьъ")


def latin(value: str, table: dict[str, str] = _CYRILLIC) -> str:
    """`value` with its Cyrillic letters written in Latin; Latin letters unchanged. With the
    German table, е at the start or after a vowel is "je" (Фадеев → Fadejew), as Duden writes it."""
    out = []
    prev = ""
    for ch in value or "":
        low = ch.lower()
        if low in table:
            mapped = table[low]
            if low == "е" and table is _CYRILLIC_DE and (not prev or prev in _CYRILLIC_VOWELS):
                mapped = "je"
            out.append(mapped.capitalize() if ch != low and mapped else mapped)
        else:
            out.append(ch)
        prev = low if low.isalpha() else ""
    return "".join(out)


# Applied in order to a folded Latin name. Each pair is one systematic difference between how
# German and English (or no scheme at all) write the same Cyrillic or German letter.
_ALTERNATIONS = (
    ("schtsch", "shch"),
    ("tsch", "ch"),
    ("sch", "sh"),
    ("zh", "sh"),
    ("kh", "ch"),
    ("tz", "ts"),
    ("w", "v"),
    ("j", "y"),
    ("x", "ks"),
)


def _translit_key(value: str) -> str:
    key = _fold(latin(value))
    for a, b in _ALTERNATIONS:
        key = key.replace(a, b)
    # A y between vowels is a glide one scheme writes and another does not (Fadeyev / Fadeev).
    key = re.sub(r"(?<=[aeiou])y(?=[aeiou])", "", key)
    # So is a leading y before e (Yelena / Jelena / Elena).
    key = re.sub(r"^ye", "e", key)
    # The ending -iy / -ij / -y / -i of a first name is one sound (Dmitriy, Dmitrij, Dmitry).
    key = re.sub(r"(iy|yy|y)$", "i", key)
    # й after a vowel is y in one scheme and i in another (Chaykovskiy / Tschaikowski).
    key = re.sub(r"(?<=[aeou])y", "i", key)
    # Doubled letters are a spelling, not a sound (Wassilij / Vasiliy).
    return re.sub(r"(.)\1+", r"\1", key)


def _keys(value: str) -> set[str]:
    """ё is written e (Khrushchev) or o (Chruschtschow): a name with it has both keys."""
    if "ё" in (value or "").lower():
        return {_translit_key(re.sub("ё", v, value, flags=re.I)) for v in ("е", "о")}
    return {_translit_key(value)}


def same_name(a: str, b: str) -> bool:
    """The same name written two ways. Exact after folding, or equal once alphabet and
    transliteration scheme are set aside — never a merely similar name."""
    if not (a and b):
        return False
    return _fold(a) == _fold(b) or bool(_keys(a) & _keys(b))


def search_terms(surname: str) -> list[str]:
    """What to search a patient register for, so a card written in another alphabet or scheme is
    among the answers: the surname as given, written in Latin both the English and the German
    way when it is Cyrillic, and its first four letters, which those schemes share for almost
    every name (Fade… finds Fadeev, Fadejew and Fadeyev). Matching then decides with `same_name`.
    """
    base = (surname or "").strip()
    if not base:
        return []
    terms = [base]
    if has_cyrillic(base):
        terms += [latin(base), latin(base, _CYRILLIC_DE)]
    stem = re.sub(r"[^a-zäöüß]", "", latin(base).lower())[:4]
    if len(stem) == 4 and len(_fold(latin(base))) > 4:
        terms.append(stem)
    return list(dict.fromkeys(t for t in terms if t))
