"use client";

import { PipecatClient } from "@pipecat-ai/client-js";
import {
  ProtobufFrameSerializer,
  WebSocketTransport,
} from "@pipecat-ai/websocket-transport";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Notice, SectionTitle } from "@/components/studio/ui";
import { api } from "@/lib/api";

type State = "idle" | "connecting" | "listening" | "speaking" | "error";
type Turn = { id: number; role: "caller" | "agent"; text: string };
type VoiceLanguage = "de" | "en" | "ru" | "ar";

export function VoiceTest({
  agentId,
  generation,
  initialLanguage = "de",
}: {
  agentId: string;
  generation: number;
  initialLanguage?: VoiceLanguage;
}) {
  const clientRef = useRef<PipecatClient | null>(null);
  const connected = useRef(false);
  const turnId = useRef(0);
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [language, setLanguage] = useState<VoiceLanguage>(initialLanguage);
  // The call is recorded server-side; keep its id so the tester can go straight to the transcript.
  const [callId, setCallId] = useState<string | null>(null);
  // Which calendar the test uses (ADR-0018). The sandbox unless the tester chooses otherwise.
  const [mode, setMode] = useState<"sandbox" | "read" | "write">("sandbox");
  const [accounts, setAccounts] = useState<{ id: string; label: string }[]>([]);
  const [account, setAccount] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    Promise.all([api.listConnections(), api.listCalendarSystems()])
      .then(([rows, systems]) => {
        const usable = rows
          .filter((c) => systems.some((s) => s.key === c.adapter))
          .map((c) => ({
            id: c.id,
            label: `${c.label || c.adapter} · ${systems.find((s) => s.key === c.adapter)?.label ?? c.adapter}`,
          }));
        setAccounts(usable);
        setAccount((current) => current || usable[0]?.id || "");
      })
      .catch(() => setAccounts([]));
  }, []);

  const appendTurn = (role: Turn["role"], text: string) => {
    if (!text.trim()) return;
    setTurns((current) => {
      turnId.current += 1;
      return [...current, { id: turnId.current, role, text: text.trim() }];
    });
  };

  useEffect(() => {
    return () => {
      void clientRef.current?.disconnect();
    };
  }, []);

  const stop = async () => {
    await clientRef.current?.disconnect();
    clientRef.current = null;
    setState("idle");
  };

  const start = async () => {
    setError(null);
    setTurns([]);
    setCallId(null);
    connected.current = false;
    setState("connecting");
    try {
      const { ws_url, conversation_id } = await api.startVoiceSession(
        language,
        agentId,
        generation,
        mode === "sandbox"
          ? { calendar_mode: "sandbox" }
          : { calendar_mode: mode, connection_id: account, confirm_real_writes: confirmed },
      );
      setCallId(conversation_id);
      const client = new PipecatClient({
        transport: new WebSocketTransport({
          serializer: new ProtobufFrameSerializer(),
        }),
        enableCam: false,
        enableMic: true,
        callbacks: {
          onConnected: () => {
            connected.current = true;
            setState("listening");
          },
          onDisconnected: () => setState("idle"),
          onUserStartedSpeaking: () => setState("listening"),
          onBotStartedSpeaking: () => setState("speaking"),
          onBotStoppedSpeaking: () => setState("listening"),
          // Each finished turn as the server stored it, phone numbers and dates written as digits:
          // the same transcript the History shows, not a second one assembled from tokens here.
          onServerMessage: (data) => {
            if (data?.type === "transcript" && (data.role === "caller" || data.role === "agent")) {
              appendTurn(data.role, String(data.text ?? ""));
            }
          },
          onError: (message) => {
            // The agent ends a finished call itself; the closed connection then reports an error.
            // After a conversation has run, that is the call ending, not a failure.
            if (connected.current) {
              setState("idle");
              return;
            }
            setError(`Voice session failed (${message.type})`);
            setState("error");
          },
        },
      });
      clientRef.current = client;
      await client.initDevices();
      await client.connect({ wsUrl: ws_url });
    } catch (cause) {
      await clientRef.current?.disconnect().catch(() => {});
      clientRef.current = null;
      setError(cause instanceof Error ? cause.message : String(cause));
      setState("error");
    }
  };

  const active =
    state === "connecting" || state === "listening" || state === "speaking";
  return (
    <div className="text-foreground">
      <main className="mx-auto max-w-5xl">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="font-display text-2xl tracking-tight text-ink">
              Try a conversation
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              Test this saved draft. No emails are sent to your practice. Use invented patient
              details.
            </p>
            <fieldset className="mt-4 max-w-2xl space-y-2 text-sm" disabled={active}>
              <legend className="font-medium text-ink">Calendar for this test</legend>
              {(
                [
                  ["sandbox", "Practice sandbox — nothing real is read or written"],
                  ["read", "Real calendar, read only — real free times; it stops before booking"],
                  ["write", "Real calendar, write test appointments — books for real, marked TEST"],
                ] as const
              ).map(([value, label]) => (
                <label key={value} className="flex items-start gap-2">
                  <input
                    type="radio"
                    name="calendar-mode"
                    className="mt-1"
                    checked={mode === value}
                    onChange={() => {
                      setMode(value);
                      setConfirmed(false);
                    }}
                  />
                  <span>{label}</span>
                </label>
              ))}
              {mode !== "sandbox" && (
                <div className="space-y-2 pl-6">
                  {accounts.length === 0 ? (
                    <p className="text-warning">
                      No calendar account yet. Connect one in Governance → Connections.
                    </p>
                  ) : (
                    <select
                      value={account}
                      onChange={(e) => setAccount(e.target.value)}
                      className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-ink sm:w-auto"
                    >
                      {accounts.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.label}
                        </option>
                      ))}
                    </select>
                  )}
                  {mode === "write" && (
                    <label className="flex items-start gap-2 text-warning">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={confirmed}
                        onChange={(e) => setConfirmed(e.target.checked)}
                      />
                      <span>
                        I understand this writes real appointments, marked “TEST”, into the
                        practice calendar, and may create a patient card for the name I give.
                        Nothing is cancelled automatically: to cancel or move a test appointment,
                        I make another call and ask the agent to cancel or move it. Appointments
                        are never deleted — a cancelled one stays in the calendar as cancelled.
                      </span>
                    </label>
                  )}
                </div>
              )}
            </fieldset>
          </div>
          <div className="flex items-center gap-3">
            <label
              className="text-sm text-muted-foreground"
              htmlFor="voice-language"
            >
              Language
            </label>
            <select
              id="voice-language"
              value={language}
              onChange={(event) =>
                setLanguage(event.target.value as VoiceLanguage)
              }
              disabled={active}
              className="rounded-md border border-border bg-surface px-3 py-2 text-sm text-ink disabled:opacity-50"
            >
              <option value="de">Deutsch</option>
              <option value="en">English</option>
              <option value="ru">Русский</option>
              <option value="ar">العربية</option>
            </select>
            <button
              type="button"
              onClick={() => void (active ? stop() : start())}
              disabled={
                state === "connecting" ||
                (!active && mode !== "sandbox" && (!account || (mode === "write" && !confirmed)))
              }
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {state === "connecting"
                ? "Connecting…"
                : active
                  ? "End conversation"
                  : "Start conversation"}
            </button>
          </div>
        </div>

        {error && (
          <div className="mt-6">
            <Notice tone="error">{error}</Notice>
          </div>
        )}

        <div className="mt-8 grid gap-5 lg:grid-cols-[240px_1fr]">
          <aside className="rounded-xl border border-border bg-surface p-5">
            <SectionTitle>Session</SectionTitle>
            <div className="mt-4 flex items-center gap-3">
              <span
                className={`h-3 w-3 rounded-full ${state === "speaking" ? "bg-warning" : active ? "bg-success" : "bg-border"}`}
              />
              <span className="font-mono text-xs uppercase text-muted-foreground">
                {state}
              </span>
            </div>
            <dl className="mt-6 space-y-3 font-mono text-xs text-muted-foreground">
              <div>
                <dt>Mode</dt>
                <dd className="text-ink">
                  {mode === "sandbox"
                    ? "Sandbox — nothing real"
                    : mode === "read"
                      ? "Real calendar, read only"
                      : "Real calendar, writes TEST bookings"}
                </dd>
              </div>
              <div>
                <dt>Draft revision</dt>
                <dd className="text-ink">{generation}</dd>
              </div>
            </dl>
            {callId && (
              <Link
                href={`/studio/history?call=${callId}`}
                className="mt-6 inline-block font-mono text-[11px] text-primary hover:underline"
              >
                View saved call →
              </Link>
            )}
          </aside>

          <section className="min-h-[420px] rounded-xl border border-border bg-surface p-5">
            <SectionTitle>Transcript</SectionTitle>
            {turns.length === 0 ? (
              <p className="mt-6 text-sm text-muted-foreground">
                Start a conversation and allow microphone access. The agent will
                greet you.
              </p>
            ) : (
              <div className="mt-5 space-y-4" aria-live="polite">
                {turns.map((turn) => (
                  <div
                    key={turn.id}
                    className={turn.role === "agent" ? "pr-8" : "pl-8"}
                  >
                    <div className="font-mono text-[10px] uppercase text-muted-foreground">
                      {turn.role}
                    </div>
                    <p
                      className={`mt-1 rounded-lg px-3 py-2 text-sm ${turn.role === "agent" ? "bg-muted" : "bg-primary/10"}`}
                    >
                      {turn.text}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
