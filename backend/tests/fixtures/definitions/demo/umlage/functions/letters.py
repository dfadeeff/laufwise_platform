from decimal import Decimal

from umlage.settle import euro


def run(input: dict, settings: dict, rules: dict) -> list[dict]:
    result, days = input["result"], int(settings.get("payment_days") or 30)
    sender = (settings.get("sender") or "").strip()
    letters = []
    for s in result["statements"]:
        balance = Decimal(s["balance"])
        if balance > 0:
            what = f"Bitte überweisen Sie {euro(s['balance'])} innerhalb von {days} Tagen."
        elif balance < 0:
            what = f"Wir erstatten Ihnen {euro(str(-balance))} innerhalb von {days} Tagen."
        else:
            what = "Ihre Zahlungen decken die Kosten genau."
        text = (
            f"Guten Tag {s['party']},\n\nauf Sie entfallen {euro(s['total'])}; gezahlt haben Sie "
            f"{euro(s['paid'])}. {what}\n\nMit freundlichen Grüßen" + (f"\n{sender}" if sender else "") + "\n"
        )
        letters.append({
            "title": f"{result['title']} – {s['party']}",
            "recipient": s["party"],
            "text": text,
            "enclosure": f"{result['title']}\n{s['party']}: Kosten {euro(s['total'])}, gezahlt {euro(s['paid'])}\n",
            "enclosure_name": f"Aufstellung {s['party']}",
            "sender": sender,
            "sender_address": (settings.get("sender_address") or "").strip(),
        })
    return letters
