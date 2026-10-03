"""A practice's own web page, read into a document, and its price list into treatments (ADR-0019).

Two things a practice already has on its website: the text callers ask about, and a price list.
`page_text` turns a page into a knowledge document the practice reviews before saving;
`price_proposals` turns a price list into treatment rows the practice confirms before they reach
an agent. Nothing fetched here is ever used unseen.

The fetch runs inside the platform, so a URL is a way to make the platform send a request. It
therefore reaches only public http(s) addresses: every hostname, including each redirect's, must
resolve to globally routable IPs only, which keeps out localhost, the private network and cloud
metadata endpoints. Responses are capped in size and time. A DNS answer that changes between this
check and the connection is not defended against; the reach of such a request is one GET whose
body is only read back as text.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

MAX_PAGE_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 10.0
MAX_REDIRECTS = 5


class WebImportError(Exception):
    """Something the practice can act on, in its words."""


# --- fetching ---------------------------------------------------------------------------------


def _public(host: str) -> bool:
    """Whether every address the host resolves to is globally routable."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        return literal.is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    addresses = {str(info[4][0]) for info in infos}
    return bool(addresses) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses)


def _checked(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebImportError("Enter a web address starting with https://")
    if parts.port not in (None, 80, 443):
        raise WebImportError("That address is not a public web page.")
    if not _public(parts.hostname):
        raise WebImportError("That address is not a public web page.")
    return parts.geturl()


def fetch(url: str, *, transport: httpx.BaseTransport | None = None) -> str:
    """The HTML of a public web page. Blocking: run it in a thread."""
    current = _checked(url)
    with httpx.Client(
        timeout=TIMEOUT_S,
        follow_redirects=False,
        transport=transport,
        headers={"User-Agent": "Mozilla/5.0 (compatible; laufwise-knowledge-import)"},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        current = _checked(urljoin(current, response.headers.get("location", "")))
                        continue
                    if response.status_code >= 400:
                        raise WebImportError(f"The page answered with an error ({response.status_code}).")
                    kind = response.headers.get("content-type", "")
                    if "html" not in kind and "text/plain" not in kind:
                        raise WebImportError("That address is not a web page.")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > MAX_PAGE_BYTES:
                            raise WebImportError("That page is too large to import.")
                    return body.decode(response.encoding or "utf-8", errors="replace")
            except httpx.HTTPError as exc:
                raise WebImportError("The page could not be reached.") from exc
    raise WebImportError("That page redirects too many times.")


# --- reading ----------------------------------------------------------------------------------

# Navigation, scripts and page furniture: never what a caller asks about.
_SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside",
         "template", "iframe", "button", "select"}
_BLOCK = {"p", "div", "li", "ul", "ol", "br", "tr", "td", "th", "section", "article", "table",
          "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "main", "body", "span"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class _Page(HTMLParser):
    """Lines of readable text, the page title, and which lines were headings."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.main_lines: list[str] = []
        self.headings: set[str] = set()
        self.title = ""
        self._skip = 0
        self._main = 0
        self._title = False
        self._heading = False
        self._buffer: list[str] = []

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        self._buffer = []
        if not text:
            return
        self.lines.append(text)
        if self._main:
            self.main_lines.append(text)
        if self._heading:
            self.headings.add(text)

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "title":
            self._title = True
        elif tag == "main":
            self._main += 1
        if tag in _BLOCK:
            self._flush()
        if tag in _HEADINGS:
            self._heading = True

    def handle_endtag(self, tag):
        if tag in _BLOCK:
            self._flush()
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._title = False
        elif tag == "main" and self._main:
            self._main -= 1
        if tag in _HEADINGS:
            self._heading = False

    def handle_data(self, data):
        if self._title:
            self.title += data
        elif not self._skip:
            self._buffer.append(data)


def _parse(html: str) -> _Page:
    page = _Page()
    page.feed(html)
    page.close()
    page._flush()
    return page


def page_text(html: str) -> tuple[str, str]:
    """`(title, text)`: the page's readable content, one line per block, `<main>` if it has one."""
    page = _parse(html)
    lines = page.main_lines or page.lines
    return re.sub(r"\s+", " ", page.title).strip(), "\n".join(lines)


_PRICE = re.compile(
    r"^(?:ab\s*)?(?:€\s*)?(\d{1,4})(?:[.,](\d{1,2}))?\s*(?:€|EUR|Euro)?$", re.IGNORECASE
)
_INLINE_PRICE = re.compile(
    r"^(?P<name>.+?)\s*[:\-–·.]*\s*(?:ab\s*)?(?P<euros>\d{1,4})(?:[.,](?P<cents>\d{1,2}))?\s*(?:€|EUR)$",
    re.IGNORECASE,
)
_DURATION = re.compile(r"^(ca\.?\s*)?\d+\s*(min|minuten|std|h)\b", re.IGNORECASE)


def _euros(euros: str, cents: str | None) -> int:
    return int(round(int(euros) + (int(cents.ljust(2, "0")) / 100 if cents else 0)))


def treatment_key(name: str) -> str:
    """`Medizinische Fußpflege` → `medizinische_fusspflege`: the form a booking carries."""
    text = name.lower()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(umlaut, plain)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:80] or "treatment"


def price_proposals(html: str) -> list[dict]:
    """Treatments and prices a price list names, for the practice to confirm.

    A price on its own line belongs to the card just above it: the card's first line that is not
    a section heading or a duration is its name. A price of 0 is left out (insurance-covered
    treatments are listed at 0, and 0 already reads as "price on request").
    """
    page = _parse(html)
    lines = page.main_lines or page.lines
    found: list[dict] = []
    keys: set[str] = set()
    previous_price = -1
    for index, line in enumerate(lines):
        name, euros = None, None
        if match := _PRICE.match(line):
            euros = _euros(match.group(1), match.group(2))
            window = lines[max(previous_price + 1, index - 4):index]
            candidates = [
                text for text in window if text not in page.headings and not _DURATION.match(text)
            ]
            name = candidates[0] if candidates else None
        elif match := _INLINE_PRICE.match(line):
            name, euros = match.group("name"), _euros(match.group("euros"), match.group("cents"))
        else:
            continue
        previous_price = index
        if not name or not euros:
            continue
        key = treatment_key(name)
        while key in keys:
            key = f"{key}_2"[:80]
        keys.add(key)
        found.append({"key": key, "name": name[:160], "price_eur": min(euros, 10000)})
    return found
