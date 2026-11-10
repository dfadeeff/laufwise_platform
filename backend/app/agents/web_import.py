"""A practice's own web page, read into a document (ADR-0019).

`page_text` turns a page (its services, prices, FAQ, whatever it holds) into a knowledge document
the practice reviews before an agent uses it. Nothing fetched here is ever used unseen.

The fetch runs inside the platform, so a URL is a way to make the platform send a request. It
therefore reaches only public http(s) addresses: every hostname, including each redirect's, must
resolve to globally routable IPs only, which keeps out localhost, the private network and cloud
metadata endpoints. The transport connects to the checked IP itself, preserving the original
hostname for Host and TLS verification. Responses are capped in size and time.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
import httpcore

MAX_PAGE_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 10.0
MAX_REDIRECTS = 5


class WebImportError(Exception):
    """Something the practice can act on, in its words."""


class PublicNetworkBackend(httpcore.SyncBackend):
    """Resolve once, check every answer, then open a socket to a checked literal IP.

    httpcore keeps the origin hostname for SNI/certificate checking. No proxy or second DNS
    lookup may replace the checked peer. Redirects and new connections get the same check.
    """

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise WebImportError("The page could not be reached.") from exc
        addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise WebImportError("That address is not a public web page.")
        # One chosen checked address; a failed connection is surfaced, never retried through DNS.
        return super().connect_tcp(
            addresses[0], port, timeout=timeout, local_address=local_address,
            socket_options=socket_options,
        )


class _ResponseStream(httpx.SyncByteStream):
    def __init__(self, stream):
        self._stream = stream

    def __iter__(self):
        yield from self._stream

    def close(self):
        self._stream.close()


class PublicHTTPTransport(httpx.BaseTransport):
    """The HTTPX/httpcore transport seam, with public-only TCP and ordinary verified TLS."""

    def __init__(self):
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(), network_backend=PublicNetworkBackend(),
        )

    def handle_request(self, request):
        response = self._pool.handle_request(httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme, host=request.url.raw_host,
                port=request.url.port, target=request.url.raw_path,
            ),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions,
        ))
        return httpx.Response(
            response.status, headers=response.headers, stream=_ResponseStream(response.stream),
            extensions=response.extensions,
        )

    def close(self):
        self._pool.close()


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
        transport=transport if transport is not None else PublicHTTPTransport(),
        trust_env=False,
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
            except (httpx.HTTPError, httpcore.NetworkError, httpcore.TimeoutException, httpcore.ProtocolError) as exc:
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
