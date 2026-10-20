"""Read a price list out of a web page: one row per price, named as the page names it (#133).

Deterministic and generic: no model, and nothing about one site's markup. A price is a text such
as "55 €", "ab 69 €" or "0 €*". Its row is the smallest element around it that also holds other
text, and no other price; the row's first other text is its name; what follows (a description, a duration) is
kept as detail. That holds for a table row, a list item or a card alike.

A page this finds nothing on yields an empty list, never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from html.parser import HTMLParser

_PRICE = re.compile(r"^(?P<from>ab\s+)?(?P<amount>\d{1,5}(?:[.,]\d{1,2})?)\s*(?:€|EUR)\s*(?P<note>\*+)?$", re.I)
_SKIP = {"script", "style", "noscript", "svg", "template", "head"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@dataclass(frozen=True)
class PriceRow:
    label: str  # the row's name, as written on the page
    text: str  # the price as written: "55 €", "ab 69 €", "0 €*"
    amount: Decimal
    kind: str  # "fixed", "from" ("ab …") or "package" (the row says Paket)
    detail: str = ""  # what else the row says: description, duration

    def public(self) -> dict:
        return {
            "label": self.label,
            "text": self.text,
            "amount": str(self.amount),
            "kind": self.kind,
            "detail": self.detail,
        }


class _Node:
    __slots__ = ("children", "parent")

    def __init__(self, parent: _Node | None) -> None:
        self.parent = parent
        self.children: list[_Node | str] = []


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node(None)
        self._at = self.root
        self._skipping = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag in _SKIP:
            self._skipping += 1
        if self._skipping or tag in _VOID:
            return
        node = _Node(self._at)
        self._at.children.append(node)
        self._at = node

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skipping = max(0, self._skipping - 1)
            return
        if self._skipping or tag in _VOID:
            return
        if self._at.parent is not None:
            self._at = self._at.parent

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text and not self._skipping:
            self._at.children.append(text)


def _texts(node: _Node) -> list[str]:
    out: list[str] = []
    for child in node.children:
        out.extend([child] if isinstance(child, str) else _texts(child))
    return out


def _price_nodes(node: _Node) -> list[tuple[_Node, str]]:
    """Every price text with the element it sits in, in page order."""
    out: list[tuple[_Node, str]] = []
    for child in node.children:
        if isinstance(child, str):
            if _PRICE.match(child):
                out.append((node, child))
        else:
            out.extend(_price_nodes(child))
    return out


def parse_prices(html: str) -> list[PriceRow]:
    tree = _Tree()
    tree.feed(html)
    tree.close()

    rows: list[PriceRow] = []
    seen: set[str] = set()
    for holder, price in _price_nodes(tree.root):
        row = _row(holder, price)
        if row is not None and row.label.casefold() not in seen:
            seen.add(row.label.casefold())
            rows.append(row)
    return rows


def _row(holder: _Node, price: str) -> PriceRow | None:
    """The row is the smallest element around the price that also says something else, and holds
    no other price: a table row, a list item, a card."""
    node: _Node | None = holder
    while node is not None:
        texts = _texts(node)
        if sum(1 for t in texts if _PRICE.match(t)) > 1:
            return None  # reached a list of prices without finding this one's name
        others = [t for t in texts if t != price]
        if others:
            break
        node = node.parent
    else:
        return None
    match = _PRICE.match(price)
    assert match is not None
    amount = Decimal(match["amount"].replace(",", "."))
    detail = " · ".join(others[1:])
    if match["from"]:
        kind = "from"
    elif re.search(r"paket", " ".join(others), re.I):
        kind = "package"
    else:
        kind = "fixed"
    return PriceRow(label=others[0], text=price, amount=amount, kind=kind, detail=detail)
