"""When the doctolib login stops on a page it does not know, the error says what that page shows.

"The browser ended on auth.doctolib.de (title 'Doctolib')" is true of every login page doctolib
has, so it could not tell a PIN screen from a password one, and only a screenshot from the practice
could. The page's headings, fields and buttons say which it is. What anyone typed never appears.
"""

from __future__ import annotations

from app.providers.doctolib_login import _page_summary


def test_the_summary_names_headings_fields_and_buttons():
    info = {
        "headings": ["PIN eingeben"],
        "fields": [{"type": "password", "name": "pin", "inputmode": "numeric", "maxlength": "4",
                    "autocomplete": "one-time-code", "placeholder": "", "label": "PIN"}],
        "buttons": ["Weiter", "PIN vergessen?"],
    }
    text = _page_summary(info)
    assert "PIN eingeben" in text
    assert "password" in text and "maxlength=4" in text and "numeric" in text and "label=PIN" in text
    assert "Weiter" in text and "PIN vergessen?" in text


def test_what_was_typed_never_appears():
    info = {"headings": [], "fields": [{"type": "email", "value": "praxis@example.de"}], "buttons": []}
    assert "praxis@example.de" not in _page_summary(info)


def test_a_page_that_could_not_be_read_still_gives_an_answer():
    assert _page_summary({}) == "headings: - · fields: - · buttons: -"
    assert _page_summary(None) == "headings: - · fields: - · buttons: -"


def test_long_pages_are_cut_short():
    info = {"headings": [], "fields": [], "buttons": [f"Button {i}" for i in range(50)]}
    assert len(_page_summary(info)) <= 600
