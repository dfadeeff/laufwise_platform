"""Whether what the caller said is a clear yes (Mia spec v3 #9, #12; issue #136).

Decided by the platform from the speech recognition's own text, never by the model: in the live
test, "Yeah. Yeah. Passed." — German misheard as English — was taken as consent. A clear yes is
an answer made only of words that say yes or are the ordinary padding around one ("ja, genau,
das passt so, danke"). Anything else — a no, a hesitation, a question, a correction, or a word
the list does not know — is not a yes, and the agent asks again. Strict on purpose: a refused yes
costs one question; an accepted non-yes is an appointment the caller did not agree to.
"""

from __future__ import annotations

import re

# Words that say yes, in the languages the agent speaks.
_YES = {
    # de
    "ja", "jawohl", "jap", "jo", "genau", "richtig", "korrekt", "stimmt", "passt", "gerne",
    "gern", "einverstanden", "perfekt", "okay", "ok", "klar", "sicher", "natürlich",
    "selbstverständlich", "super", "prima", "bestens", "buchen", "bitte",
    # en
    "yes", "yeah", "yep", "yup", "correct", "right", "sure", "absolutely", "definitely",
    "book", "fine", "great",
    # ru
    "да", "конечно", "верно", "правильно", "хорошо", "давайте", "ага", "точно", "отлично",
    "согласен", "согласна", "бронируйте", "запишите", "подходит",
}
# Words that only pad a yes: they add no new information and cannot turn it into a no.
_PADDING = {
    # de
    "das", "ist", "alles", "so", "sehr", "gut", "danke", "vielen", "dank", "schön", "dankeschön",
    "sie", "können", "kann", "den", "termin", "gerade", "machen", "mir", "mich", "es", "auch",
    "ganz", "wunderbar", "toll", "doch", "ähm", "äh", "hm", "mhm", "na", "dann", "und", "ich",
    "habe", "hab", "verstanden", "verbindlich", "bitteschön", "dankeschön", "wir", "nehmen",
    "machen", "würde", "mich", "freuen", "passt's", "gebucht", "werden",
    # en
    "that", "is", "that's", "it", "it's", "all", "please", "thanks", "thank", "you", "very",
    "much", "good", "go", "ahead", "do", "a", "the", "appointment", "of", "course", "and",
    "i", "agree", "um", "uh", "so", "perfect", "lovely", "it",
    # ru
    "это", "всё", "все", "так", "спасибо", "большое", "пожалуйста", "можно", "можете", "мне",
    "меня", "запись", "записать", "запишите", "очень", "ну", "и", "вы", "да-да",
}
# Any of these makes the answer something other than a yes, whatever else it says.
_NOT_YES = {
    "nein", "nee", "nö", "nicht", "kein", "keine", "falsch", "moment", "warten", "warte",
    "stopp", "halt", "aber", "ändern", "anders", "lieber", "eigentlich", "vielleicht",
    "no", "not", "nope", "wrong", "wait", "but", "change", "maybe", "actually",
    "нет", "не", "неправильно", "подождите", "стоп", "но", "может", "изменить",
}


def clear_yes(said: str) -> bool:
    text = (said or "").strip()
    if not text or text.endswith("?"):
        return False
    words = re.findall(r"[\w'’-]+", text.casefold())
    if not words or len(words) > 14:
        return False
    if any(w in _NOT_YES for w in words):
        return False
    if not any(w in _YES for w in words):
        return False
    return all(w in _YES or w in _PADDING for w in words)
