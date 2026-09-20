import type { AgentConfig } from "./types";
import { Field, Section } from "./Fields";
export function ConfigEditor({
  section,
  config: c,
  change,
}: {
  section: string;
  config: AgentConfig;
  change: (patch: Partial<AgentConfig>) => void;
}) {
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
        </Section>
      </>
    );
  if (section === "capabilities")
    return (
      <>
        <Section
          title="What your agent can do"
          description="Permissions are enforced by the runtime, in addition to the agent’s instructions."
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
          <div className="border-t border-border pt-5">
            <p className="studio-label">Practice questions & callbacks</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Available. Questions outside the configured knowledge go to your
              team.
            </p>
          </div>
          <div className="rounded-lg bg-muted/60 p-4">
            <p className="text-sm font-medium">
              Changes and cancellations go to staff
            </p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Thevea appointment changes are not supported. The agent takes a
              callback request instead.
            </p>
          </div>
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
        <details>
          <summary className="cursor-pointer text-sm font-medium">
            Advanced voice settings
          </summary>
          <div className="mt-4">
            {input(
              "voice_id",
              "ElevenLabs voice ID",
              "Leave empty to use your administrator’s configured voice. Provider credentials stay on the server.",
            )}
          </div>
        </details>
      </Section>
    );
  return null;
}
