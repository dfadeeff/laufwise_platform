// Twilio's fallback for every platform number: what a caller hears when the voice backend does not
// answer (down, crashing, mid-deploy). Served by the frontend on purpose — a fallback on the
// backend would fail exactly when it is needed.
//
// The backend writes `?transfer=<staff number>` into the number's fallback URL when an agent is
// activated. With it the caller is put through to the practice's staff; without it they hear a
// sentence and the call ends, never Twilio's error tone. No secret is needed: this only returns
// TwiML, which does nothing unless Twilio is executing a call on one of our numbers.

const E164 = /^\+[1-9]\d{6,14}$/;

const SORRY =
  "Entschuldigung, unsere digitale Assistenz ist gerade nicht erreichbar.";

function twiml(transfer: string | null): string {
  const say = (text: string) => `<Say language="de-DE">${text}</Say>`;
  const body =
    transfer && E164.test(transfer)
      ? say(`${SORRY} Ich verbinde Sie mit der Praxis.`) +
        `<Dial timeout="25">${transfer}</Dial>` +
        say("Leider ist gerade niemand erreichbar. Bitte rufen Sie später noch einmal an.")
      : say(`${SORRY} Bitte rufen Sie später noch einmal an.`) + "<Hangup/>";
  return `<?xml version="1.0" encoding="UTF-8"?><Response>${body}</Response>`;
}

function respond(request: Request): Response {
  const transfer = new URL(request.url).searchParams.get("transfer");
  return new Response(twiml(transfer), {
    headers: { "Content-Type": "text/xml; charset=utf-8", "Cache-Control": "no-store" },
  });
}

export const POST = respond;
export const GET = respond;
