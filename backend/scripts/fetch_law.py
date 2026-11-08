"""Write a module's reference text of a law from its official XML (ADR-0041 step 1).

    python -m scripts.fetch_law --law betrkv --cites BetrKV \\
        --out definitions/immobilien/references/betrkv.md
    python -m scripts.fetch_law --law bgb --cites BGB --only "§ 556" --only "§ 556a" --only "§ 558" \\
        --title "BGB, Mietrecht: §§ 556 bis 560" --out definitions/immobilien/references/bgb-miete.md

The text comes from gesetze-im-internet.de, the federal government's publication of the law
(an official work, § 5 UrhG), as it stands on the day it is fetched. The file says where it came
from and which consolidated version it is; it is reviewed as a diff like any other file.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

import httpx

SOURCE = "https://www.gesetze-im-internet.de/{law}/"


def _text(node: ET.Element) -> str:
    """A norm's text as plain paragraphs; numbered list items on lines of their own."""
    out: list[str] = []

    def walk(el: ET.Element) -> str:
        parts = [el.text or ""]
        for child in el:
            if child.tag in ("DL",):
                parts.append("\n" + walk(child) + "\n")
            elif child.tag == "DT":
                parts.append(f"\n{walk(child).strip()} ")
            elif child.tag == "BR":
                parts.append("\n")
            elif child.tag in ("FnR", "SUP"):
                pass  # footnote markers
            elif child.tag == "LA" and "".join(parts)[-1:] not in ("", " ", "\n"):
                parts.append(" " + walk(child))  # a new line in the official layout
            else:
                parts.append(walk(child))
            parts.append(child.tail or "")
        return "".join(parts)

    for p in node.iter("P"):
        line = re.sub(r"[ \t]+", " ", walk(p)).strip()
        line = re.sub(r"\n\s*", "\n", line)
        if line:
            out.append(line)
    return "\n\n".join(out)


def fetch(law: str) -> ET.Element:
    res = httpx.get(SOURCE.format(law=law) + "xml.zip", follow_redirects=True, timeout=60)
    res.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(res.content))
    (name,) = [n for n in archive.namelist() if n.endswith(".xml")]
    return ET.fromstring(archive.read(name))


def render(root: ET.Element, law: str, cites: list[str], only: list[str], title: str | None) -> str:
    norms = root.findall("norm")
    meta = norms[0].find("metadaten")
    stand = " ".join(s.findtext("standkommentar") or "" for s in meta.findall("standangabe")).strip()
    long_title = meta.findtext("langue") or meta.findtext("kurzue") or law
    sections = []
    for norm in norms[1:]:
        m = norm.find("metadaten")
        number = (m.findtext("enbez") or "").strip()
        if not number.startswith("§") or (only and number not in only):
            continue
        content = norm.find("textdaten/text/Content")
        if content is None:
            continue
        heading = f"{number} {(m.findtext('titel') or '').strip()}".strip()
        sections.append(f"## {heading}\n\n{_text(content)}")
    head = [
        "---",
        f"title: {json.dumps(title or long_title, ensure_ascii=False)}",
        f"cites: {json.dumps(cites, ensure_ascii=False)}",
        f"url: {SOURCE.format(law=law)}",
        f"stand: {json.dumps(stand or 'not stated', ensure_ascii=False)}",
        f"retrieved: {date.today().isoformat()}",
        "---",
        "",
    ]
    return "\n".join(head) + "\n\n".join(sections) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--law", required=True, help="the law's short name on gesetze-im-internet.de")
    p.add_argument("--cites", action="append", required=True, help="how a citation names this law")
    p.add_argument("--only", action="append", default=[], help="a § to keep, exactly as numbered (\"§ 556a\")")
    p.add_argument("--title")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    text = render(fetch(args.law), args.law, args.cites, args.only, args.title)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"{args.out}: {len(text):,} characters")


if __name__ == "__main__":
    main()
