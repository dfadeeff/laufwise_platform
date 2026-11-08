"""The law Betriebskosten applies, as the module ships it (ADR-0041): the sources the expert's
review asks for, from the official text."""

from __future__ import annotations

from app.kernel.definitions import DEFINITIONS, read_definition


def test_it_carries_the_betrkv_the_heizkostenv_and_the_tenancy_sections_of_the_bgb() -> None:
    content = read_definition(DEFINITIONS / "immobilien" / "betriebskosten", DEFINITIONS / "immobilien")
    by_name = {r["name"]: r for r in content["references"]}
    assert list(by_name) == ["betrkv", "heizkostenv", "bgb-miete"]
    assert all(r["url"].startswith("https://www.gesetze-im-internet.de/") for r in by_name.values())
    assert "## § 9b Kostenaufteilung bei Nutzerwechsel" in by_name["heizkostenv"]["text"]
    for section in ("§ 556 ", "§ 556a ", "§ 558 ", "§ 558a ", "§ 558b ", "§ 559 ", "§ 560 "):
        assert f"## {section}" in by_name["bgb-miete"]["text"]
