import { useEffect, useState } from "react";
import type { AgentCapability, AgentConfig, AgentSystems } from "./types";
import { Field, Section } from "./Fields";
import { api } from "@/lib/api";
import type { KnowledgeDocument } from "@/types";
export function ConfigEditor({
  section,
  config: c,
  change,
  commit,
  systems,
}: {
  section: string;
  config: AgentConfig;
  change: (patch: Partial<AgentConfig>) => void;
  /** Apply and save at once — for decisions like taking over prices or adding a document. */
  commit?: (patch: Partial<AgentConfig>) => void;
  systems?: AgentSystems;
}) {
  // Asked for rather than hardcoded: a capability added to the platform appears here without a
  // second list to keep in sync, and the practice never sees a toggle the runtime does not have.
  const [capabilities, setCapabilities] = useState<AgentCapability[]>([]);
  useEffect(() => {
    api.listCapabilities().then(setCapabilities).catch(() => setCapabilities([]));
  }, []);
  // The workspace's documents (ADR-0017); null while loading, so "none yet" is not a flash.
  const [documents, setDocuments] = useState<KnowledgeDocument[] | null>(null);
  const [knowledgeLimit, setKnowledgeLimit] = useState(40000);
  useEffect(() => {
    if (section !== "knowledge") return;
    api
      .listKnowledge()
      .then((r) => {
        setDocuments(r.documents);
        setKnowledgeLimit(r.max_agent_chars);
      })
      .catch(() => setDocuments([]));
  }, [section]);
  const knownChars = (documents ?? [])
    .filter((d) => (c.knowledge_ids ?? []).includes(d.id))
    .reduce((sum, d) => sum + d.chars, 0);

  /** What this capability acts on, and whether the agent has it. A capability that needs a
   *  calendar and has none is switched on and unable to do anything, which the Studio used to
   *  show as a confident green tick because the binding lived two sections away. */
  const systemFor = (capability: AgentCapability) => {
    if (!capability.requires.includes("calendar")) {
      return <span className="text-muted-foreground">Needs no outside system.</span>;
    }
    const bound = systems?.calendar.bound;
    if (!bound) {
      return (
        <span className="text-warning">
          Needs a calendar. Connect one in Phone &amp; handoff — until then this does nothing on a
          real call.
        </span>
      );
    }
    if (!bound.configured) {
      return (
        <span className="text-warning">
          {bound.label} is connected but has no calendar mapping, so no times can be read or
          booked. Add it in Governance → Connections.
        </span>
      );
    }
    if (capability.name === "book_appointment" && bound.capabilities && !bound.capabilities.includes("booking")) {
      return (
        <span className="text-warning">
          {bound.label} cannot take bookings by phone yet. On calls your agent tells callers which
          times are free and passes their booking request to your team.
        </span>
      );
    }
    return (
      <span className="text-success">Acts on {bound.label}.</span>
    );
  };
  const input = (
    key: keyof AgentConfig,
    label: string,
    hint?: string,
    type = "text",
  ) => (
    <Field label={label} hint={hint}>
      <input
        className="studio-input"
        type={type}
        value={String(c[key])}
        onChange={(e) => change({ [key]: e.target.value })}
      />
    </Field>
  );
  if (section === "instructions")
    return (
      <>
        <Section
          title="A familiar welcome"
          description="Give your receptionist a name and a natural way to start the conversation."
        >
          {input("name", "Agent name", "Only your team sees this name.")}
          <Field
            label="Opening greeting"
            hint="The agent translates this into the caller’s selected language."
          >
            <textarea
              rows={3}
              className="studio-input"
              value={c.greeting}
              onChange={(e) => change({ greeting: e.target.value })}
              placeholder="Hello, you’re speaking with our practice’s digital receptionist. How can I help?"
            />
          </Field>
        </Section>
        <Section
          title="Conversation style"
          description="Describe how the agent should speak. Booking verification and patient checks always apply."
        >
          <Field label="Instructions">
            <textarea
              className="studio-input"
              rows={6}
              value={c.instructions}
              onChange={(e) => change({ instructions: e.target.value })}
            />
          </Field>
        </Section>
      </>
    );
  if (section === "knowledge")
    return (
      <>
        <Section
          title="Your practice"
          description="These facts are used in the conversation and appointment confirmations."
        >
          {input("practice_name", "Practice name")}
          <div className="grid gap-5 sm:grid-cols-2">
            {input("street", "Street and number")}
            {input("city", "City")}
            {input("postcode", "Postcode")}
            {input("phone", "Practice phone")}
            {input("email", "Practice email", undefined, "email")}
            {input("timezone", "Timezone", "For example Europe/Berlin")}
          </div>
        </Section>
        <Section
          title="Opening hours"
          description="Appointments are offered only inside these periods. The same hours apply to the selected days."
        >
          <div className="flex flex-wrap gap-2">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day, i) => (
              <button
                key={day}
                type="button"
                aria-pressed={c.weekdays.includes(i)}
                onClick={() =>
                  change({
                    weekdays: c.weekdays.includes(i)
                      ? c.weekdays.filter((d) => d !== i)
                      : [...c.weekdays, i],
                  })
                }
                className={`rounded-lg border px-3 py-2 text-sm ${c.weekdays.includes(i) ? "border-primary/30 bg-accent text-primary" : "border-border"}`}
              >
                {day}
              </button>
            ))}
          </div>
          <div className="grid gap-5 sm:grid-cols-2">
            {input("open_from", "Opens", undefined, "time")}
            {input("open_until", "Closes", undefined, "time")}
            {input(
              "break_from",
              "Break starts",
              "Leave both break fields empty for no break.",
              "time",
            )}
            {input("break_until", "Break ends", undefined, "time")}
          </div>
          <Field label="Appointment length">
            <select
              className="studio-input"
              value={c.slot_minutes}
              onChange={(e) => change({ slot_minutes: Number(e.target.value) })}
            >
              {[15, 20, 30, 45, 60].map((m) => (
                <option key={m} value={m}>
                  {m} minutes
                </option>
              ))}
            </select>
          </Field>
        </Section>
        <Section
          title="Treatments"
          description="All listed treatments use the appointment length above. The first is the default when the caller has no preference."
        >
          {c.treatments.map((t, i) => (
            <div
              key={i}
              className="grid gap-3 rounded-lg border border-border p-4 sm:grid-cols-[1fr_100px_auto]"
            >
              <Field label="Treatment">
                <input
                  className="studio-input"
                  value={t.name}
                  onChange={(e) =>
                    change({
                      treatments: c.treatments.map((v, j) =>
                        j === i ? { ...v, name: e.target.value } : v,
                      ),
                    })
                  }
                />
              </Field>
              <Field label="Price (€)">
                <input
                  className="studio-input"
                  type="number"
                  min={0}
                  value={t.price_eur}
                  onChange={(e) =>
                    change({
                      treatments: c.treatments.map((v, j) =>
                        j === i
                          ? { ...v, price_eur: Number(e.target.value) }
                          : v,
                      ),
                    })
                  }
                />
              </Field>
              <button
                type="button"
                className="self-end py-3 text-xs text-danger"
                onClick={() =>
                  change({ treatments: c.treatments.filter((_, j) => j !== i) })
                }
              >
                Remove
              </button>
            </div>
          ))}
          <button
            type="button"
            className="studio-secondary"
            onClick={() =>
              change({
                treatments: [
                  ...c.treatments,
                  {
                    key: `treatment_${crypto.randomUUID().replaceAll("-", "")}`,
                    name: "New treatment",
                    price_eur: 0,
                  },
                ],
              })
            }
          >
            + Add treatment
          </button>
          <PriceImport
            existing={c.treatments}
            onApply={(treatments) => (commit ?? change)({ treatments })}
          />
        </Section>
        <Section
          title="Documents"
          description="Your FAQ, insurance rules, a PDF or a page of your website. The agent answers from the ticked documents; anything they do not cover becomes a callback. Every document is also available to your other agents. Changes reach callers when you publish."
        >
          {documents === null ? (
            <p className="text-sm text-muted-foreground">Loading documents…</p>
          ) : documents.length === 0 ? (
            <p className="text-sm text-muted-foreground">No documents yet. Add the first one below.</p>
          ) : (
            <>
              {documents.map((d) => (
                <label key={d.id} className="flex items-start gap-3 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={(c.knowledge_ids ?? []).includes(d.id)}
                    onChange={(e) =>
                      change({
                        knowledge_ids: e.target.checked
                          ? [...(c.knowledge_ids ?? []), d.id]
                          : (c.knowledge_ids ?? []).filter((id) => id !== d.id),
                      })
                    }
                  />
                  <span>
                    {d.title}
                    <span className="text-muted-foreground"> · {d.chars.toLocaleString()} characters</span>
                  </span>
                </label>
              ))}
              <p
                className={`text-xs ${knownChars > knowledgeLimit ? "text-danger" : "text-muted-foreground"}`}
              >
                {knownChars.toLocaleString()} of at most {knowledgeLimit.toLocaleString()} characters.
                {knownChars > knowledgeLimit ? " Remove a document before publishing." : ""}
              </p>
            </>
          )}
          <AddDocument
            onAdded={(doc) => {
              setDocuments((current) => [...(current ?? []), doc]);
              // A document added from here is meant for this agent: tick it straight away.
              (commit ?? change)({ knowledge_ids: [...(c.knowledge_ids ?? []), doc.id] });
            }}
          />
        </Section>
      </>
    );
  if (section === "capabilities")
    return (
      <>
        <Section
          title="What your agent can do"
          description="Each capability, and the system it acts on. A capability is enforced by the runtime — switching one off removes its tools, it does not merely discourage them."
        >
          <label className="flex items-start justify-between gap-5">
            <div>
              <span className="studio-label">Book appointments</span>
              <p className="mt-1 text-sm text-muted-foreground">
                Check availability, collect patient details and verify the
                booking.
              </p>
            </div>
            <input
              aria-label="Allow booking appointments"
              className="mt-1 h-5 w-5 accent-primary"
              type="checkbox"
              checked={c.booking_enabled}
              onChange={(e) => change({ booking_enabled: e.target.checked })}
            />
          </label>
          {capabilities.map((capability) => {
            // `null` means every capability, which is what an agent published before this
            // existed has. The first toggle writes the list out explicitly.
            const on = c.skills === null || c.skills.includes(capability.name);
            return (
              <label
                key={capability.name}
                className="flex items-start justify-between gap-5 border-t border-border pt-5"
              >
                <div>
                  <span className="studio-label">
                    {capability.display_name}
                  </span>
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">
                    {capability.description}
                  </p>
                  <p className="mt-1.5 text-sm">{systemFor(capability)}</p>
                </div>
                <input
                  aria-label={capability.display_name}
                  className="mt-1 h-5 w-5 accent-primary"
                  type="checkbox"
                  checked={on}
                  onChange={(e) => {
                    const every = capabilities.map((s) => s.name);
                    const current = c.skills ?? every;
                    change({
                      skills: e.target.checked
                        ? every.filter((n) => current.includes(n) || n === capability.name)
                        : current.filter((n) => n !== capability.name),
                    });
                  }}
                />
              </label>
            );
          })}
          <div className="rounded-lg bg-muted/60 p-4">
            <p className="text-sm font-medium">
              Changes and cancellations go to staff
            </p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Whether an appointment can be moved or cancelled by phone depends on the connected
              calendar, not on this setting
              {systems?.calendar.bound ? ` — ${systems.calendar.bound.label} does not support it` : ""}
              . The agent takes a callback request instead, and never claims a change it could not
              make.
            </p>
          </div>
        </Section>
        <Section
          title="Returning callers"
          description="What your agent may say to someone before it has checked who they are."
        >
          <Field label="When a number you have heard before calls">
            <select
              className="studio-input"
              value={c.recall_policy}
              onChange={(e) =>
                change({
                  recall_policy: e.target
                    .value as AgentConfig["recall_policy"],
                })
              }
            >
              <option value="off">Treat every call as a first call</option>
              <option value="greeting">Greet them by name</option>
              <option value="full">Greet them and state their next appointment</option>
            </select>
          </Field>
          {c.recall_policy === "full" && (
            <label className="flex items-start gap-4 rounded-lg border border-warning/40 bg-warning/5 p-4">
              <input
                aria-label="Accept reading an appointment to an unverified caller"
                className="mt-1 h-5 w-5 shrink-0 accent-primary"
                type="checkbox"
                checked={c.recall_acknowledged}
                onChange={(e) =>
                  change({ recall_acknowledged: e.target.checked })
                }
              />
              <span className="text-sm leading-6">
                I understand that anyone holding this phone — a partner, a
                child, whoever bought the number next — will hear the patient’s
                surname and next appointment time without being asked to
                identify themselves. Changing or cancelling still requires a
                date of birth.
              </span>
            </label>
          )}
          <p className="text-sm leading-6 text-muted-foreground">
            Nothing about the appointment is stored: it is read from your
            calendar at the moment of the call. Callers are forgotten after 180
            days, and you can forget everyone at once in Phone &amp; handoff.
          </p>
        </Section>
        <Section
          title="Patient information"
          description="Required identity details, explicit confirmation and calendar verification cannot be disabled."
        >
          {input(
            "consent_policy_id",
            "Approved privacy policy reference",
            "The policy reference recorded when a patient card is created.",
          )}
          <Field
            label="Keep call transcripts for"
            hint="Days, between 1 and 365. Transcripts are deleted automatically after this; the call itself stays listed. Callers and staff are told this number."
          >
            <input
              className="studio-input"
              type="number"
              min={1}
              max={365}
              value={c.transcript_retention_days}
              onChange={(e) =>
                change({ transcript_retention_days: Number(e.target.value) })
              }
            />
          </Field>
        </Section>
      </>
    );
  if (section === "voice")
    return (
      <Section
        title="Voice & language"
        description="Choose the starting language. The current multilingual pipeline also supports switching between German, English and Russian."
      >
        <Field label="Starting language">
          <select
            className="studio-input"
            value={c.locale}
            onChange={(e) =>
              change({ locale: e.target.value as AgentConfig["locale"] })
            }
          >
            <option value="de">Deutsch</option>
            <option value="en">English</option>
            <option value="ru">Русский</option>
            <option value="ar">العربية</option>
          </select>
        </Field>
        <div className="rounded-lg bg-accent/40 p-4 text-sm leading-6">
          Listen to your agent in Tests. Your draft is saved before testing so
          you hear the configuration you just edited.
        </div>
        <Field
          label="Voice engine"
          hint="Speech-to-speech answers faster and can be interrupted mid-sentence. The standard engine transcribes, thinks, then speaks."
        >
          <select
            className="studio-input"
            value={c.voice_engine}
            onChange={(e) =>
              change({
                voice_engine: e.target.value as AgentConfig["voice_engine"],
              })
            }
          >
            <option value="cascaded">Standard</option>
            <option value="realtime">Speech-to-speech</option>
          </select>
        </Field>
        {c.voice_engine === "realtime" && c.locale === "ar" && (
          <p className="rounded-lg bg-warning/10 p-4 text-sm leading-6">
            Arabic runs on the standard engine. Switch the engine back, or
            choose another language, before publishing.
          </p>
        )}
        <details>
          <summary className="cursor-pointer text-sm font-medium">
            Advanced voice settings
          </summary>
          <div className="mt-4">
            {c.voice_engine === "realtime" ? (
              <p className="text-sm leading-6 text-muted-foreground">
                A speech-to-speech agent speaks with its own voice, so the
                ElevenLabs voice is not used. Clear it before publishing, or
                switch back to the standard engine.
              </p>
            ) : (
              input(
                "voice_id",
                "ElevenLabs voice ID",
                "Leave empty to use your administrator’s configured voice. Provider credentials stay on the server.",
              )
            )}
          </div>
        </details>
      </Section>
    );
  return null;
}


/** Read a price list from the practice's website into treatment rows to confirm (ADR-0019).
 *  Nothing reaches the agent until the practice ticks rows here and then saves and publishes. */
function PriceImport({
  existing,
  onApply,
}: {
  existing: AgentConfig["treatments"];
  onApply: (treatments: AgentConfig["treatments"]) => void;
}) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [found, setFound] = useState<AgentConfig["treatments"]>([]);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const known = new Set(existing.map((t) => t.key));

  async function read() {
    setBusy(true);
    setError("");
    setFound([]);
    try {
      const { treatments } = await api.proposePrices(url.trim());
      setFound(treatments);
      setChosen(new Set(treatments.map((t) => t.key)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function apply() {
    const picked = found.filter((t) => chosen.has(t.key));
    const byKey = new Map(picked.map((t) => [t.key, t]));
    // A treatment the agent already has keeps its place and takes the page's name and price.
    const updated = existing.map((t) => byKey.get(t.key) ?? t);
    const added = picked.filter((t) => !known.has(t.key));
    onApply([...updated, ...added]);
    setFound([]);
    setUrl("");
  }

  return (
    <div className="rounded-lg border border-dashed border-border p-4">
      <p className="text-sm font-medium">Import prices from your website</p>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">
        Paste the address of your price page. You choose which treatments to take over, then save
        and publish as usual.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <input
          className="studio-input min-w-0 flex-1 basis-64"
          inputMode="url"
          placeholder="https://www.ihre-praxis.de/preise"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button
          type="button"
          className="studio-secondary"
          disabled={busy || !url.trim()}
          onClick={() => void read()}
        >
          {busy ? "Reading…" : "Read prices"}
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      {found.length > 0 && (
        <div className="mt-3 space-y-2">
          {found.map((t) => (
            <label key={t.key} className="flex items-start gap-3 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={chosen.has(t.key)}
                onChange={(e) => {
                  const next = new Set(chosen);
                  if (e.target.checked) next.add(t.key);
                  else next.delete(t.key);
                  setChosen(next);
                }}
              />
              <span>
                {t.name} · {t.price_eur} €
                {known.has(t.key) && (
                  <span className="text-muted-foreground"> · updates the existing one</span>
                )}
              </span>
            </label>
          ))}
          <button
            type="button"
            className="studio-primary"
            disabled={chosen.size === 0}
            onClick={apply}
          >
            Take over {chosen.size} treatment{chosen.size === 1 ? "" : "s"} and save
          </button>
        </div>
      )}
    </div>
  );
}


/** Add a document without leaving the agent: paste text, upload a PDF, or import a web page.
 *  It lands in the workspace's shared Documents, so other agents can tick it too. */
function AddDocument({ onAdded }: { onAdded: (doc: KnowledgeDocument) => void }) {
  const [kind, setKind] = useState<"text" | "url" | "pdf">("text");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function add(file?: File) {
    setBusy(true);
    setError("");
    try {
      let doc: KnowledgeDocument;
      if (kind === "url") doc = await api.addKnowledgeUrl(url.trim(), title.trim());
      else if (kind === "pdf" && file) {
        const data = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
          reader.onerror = () => reject(new Error("The file could not be read."));
          reader.readAsDataURL(file);
        });
        doc = await api.addKnowledgePdf(title.trim() || file.name.replace(/\.pdf$/i, ""), data);
      } else doc = await api.addKnowledgeText(title.trim(), text);
      onAdded(doc);
      setTitle("");
      setText("");
      setUrl("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-dashed border-border p-4">
      <p className="text-sm font-medium">Add a document</p>
      <div className="flex flex-wrap gap-2" role="radiogroup">
        {(
          [
            ["text", "Paste text"],
            ["url", "Web page"],
            ["pdf", "Upload PDF"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={kind === value}
            className={kind === value ? "studio-primary" : "studio-secondary"}
            onClick={() => setKind(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <input
        className="studio-input"
        maxLength={200}
        placeholder={kind === "text" ? "Title, e.g. Versicherung und Rezepte" : "Title (optional)"}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      {kind === "text" && (
        <textarea
          rows={6}
          className="studio-input font-normal"
          placeholder="Paste the text the agent should know."
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      )}
      {kind === "url" && (
        <input
          className="studio-input"
          inputMode="url"
          placeholder="https://www.ihre-praxis.de/faq"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      )}
      {error && <p className="text-sm text-danger">{error}</p>}
      {kind === "pdf" ? (
        <label className="studio-primary inline-flex cursor-pointer">
          {busy ? "Uploading…" : "Choose a PDF"}
          <input
            type="file"
            accept="application/pdf,.pdf"
            className="sr-only"
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void add(file);
              e.target.value = "";
            }}
          />
        </label>
      ) : (
        <button
          type="button"
          className="studio-primary"
          disabled={
            busy || (kind === "text" ? !title.trim() || !text.trim() : !url.trim())
          }
          onClick={() => void add()}
        >
          {busy ? "Adding…" : kind === "url" ? "Import page" : "Add document"}
        </button>
      )}
    </div>
  );
}
