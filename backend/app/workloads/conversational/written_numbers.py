"""Phone numbers and dates in a transcript, written as a person would write them down.

On a call both sides SAY numbers ("null eins fünf zwei …", "vierzehnter November
neunzehnhundertzweiundneunzig"), and the speech-to-text and the agent's own words record them as
words. Read back in the History or the test page, that is unreadable. `as_written` rewrites just
two shapes into digits:

- a run of four or more single spoken digits → `015259362157`
- a day, a month name and optionally a year → `14.11.1992`

Nothing else is touched: "ein Termin" stays a word, a time ("halb elf") stays as said. It is for
what a person READS — the model and the voice never see its output, so a conversation that works
is not changed by it.
"""

from __future__ import annotations

import re

_DIGIT_WORDS = {
    "null": "0", "eins": "1", "zwei": "2", "zwo": "2", "drei": "3", "vier": "4", "fünf": "5",
    "sechs": "6", "sieben": "7", "acht": "8", "neun": "9",
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_DIGIT = r"(?:" + "|".join(sorted(_DIGIT_WORDS, key=len, reverse=True)) + r"|\d)"
# Four or more single digits, separated by spaces, commas or hyphens ("null eins fünf, zwei …").
_DIGIT_RUN = re.compile(rf"(?<!\w){_DIGIT}(?:[\s,\-]+{_DIGIT}){{3,}}(?!\w)", re.IGNORECASE)

_MONTHS = {
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_UNITS = {
    "ein": 1, "eins": 1, "eine": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9,
}
_TEENS = {
    "zehn": 10, "elf": 11, "zwölf": 12, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15,
    "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19,
}
_TENS = {
    "zwanzig": 20, "dreißig": 30, "vierzig": 40, "fünfzig": 50, "sechzig": 60, "siebzig": 70,
    "achtzig": 80, "neunzig": 90,
}
# Ordinal stems whose cardinal is not the stem itself: "erste", "dritte", "siebte", "achte".
_ORDINAL_STEMS = {"er": "eins", "ers": "eins", "drit": "drei", "sieb": "sieben", "ach": "acht"}


def _below_hundred(word: str) -> int | None:
    if word in _UNITS:
        return _UNITS[word]
    if word in _TEENS:
        return _TEENS[word]
    if word in _TENS:
        return _TENS[word]
    if "und" in word:
        unit, _, tens = word.partition("und")
        if unit in _UNITS and tens in _TENS:
            return _UNITS[unit] + _TENS[tens]
    return None


def german_number(word: str) -> int | None:
    """A German cardinal written as one word, 1–9999 ("neunzehnhundertzweiundneunzig" → 1992)."""
    word = word.lower()
    if word.isdigit():
        return int(word)
    total = 0
    if "tausend" in word:
        head, _, word = word.partition("tausend")
        thousands = _below_hundred(head) if head else 1
        if thousands is None:
            return None
        total += thousands * 1000
    if "hundert" in word:
        head, _, word = word.partition("hundert")
        hundreds = _below_hundred(head) if head else 1
        if hundreds is None:
            return None
        total += hundreds * 100
    if word:
        rest = _below_hundred(word)
        if rest is None:
            return None
        total += rest
    return total or None


def _ordinal(word: str) -> int | None:
    """ "vierzehnter", "dritten", "1." → the day number, or None."""
    word = word.lower().rstrip(".")
    if word.isdigit():
        return int(word)
    stem = re.sub(r"(?:ste|te)[nrsm]?$", "", word)
    if stem == word:
        return None
    return german_number(_ORDINAL_STEMS.get(stem, stem))


_DATE = re.compile(
    r"(?<!\w)(\d{1,2}\.|[a-zäöüß]+(?:ste|te)[nrsm]?)\s+(" + "|".join(_MONTHS) + r")"
    r"(?:\s+(\d{2,4}|[a-zäöüß]+))?(?!\w)",
    re.IGNORECASE,
)


def _date(match: re.Match) -> str:
    day = _ordinal(match.group(1))
    month = _MONTHS[match.group(2).lower()]
    if day is None or not 1 <= day <= 31:
        return match.group(0)
    written = f"{day:02d}.{month:02d}."
    year_word = match.group(3)
    if year_word:
        year = german_number(year_word)
        if year is None:
            # Not a year after all ("am vierzehnten November gerne"): keep the word.
            return f"{written} {year_word}"
        written += str(year)
    return written


def _digits(match: re.Match) -> str:
    return "".join(_DIGIT_WORDS.get(t.lower()) or t for t in re.findall(_DIGIT, match.group(0), re.IGNORECASE))


def as_written(text: str) -> str:
    return _DATE.sub(_date, _DIGIT_RUN.sub(_digits, text))
