"use client";

import { PipecatClient } from "@pipecat-ai/client-js";
import {
  ProtobufFrameSerializer,
  WebSocketTransport,
} from "@pipecat-ai/websocket-transport";
import { useEffect, useRef, useState } from "react";

import { StudioHeader } from "@/components/studio/StudioHeader";
import { Notice, SectionTitle } from "@/components/studio/ui";
import { api } from "@/lib/api";

type State = "idle" | "connecting" | "listening" | "speaking" | "error";
type Turn = { id: number; role: "caller" | "agent"; text: string };

export default function VoiceTestPage() {
  const clientRef = useRef<PipecatClient | null>(null);
  const turnId = useRef(0);
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);

  const appendTurn = (role: Turn["role"], text: string, aggregate = false) => {
    setTurns((current) => {
      const last = current.at(-1);
      if (aggregate && last?.role === role) {
        return [...current.slice(0, -1), { ...last, text: `${last.text}${text}` }];
      }
      turnId.current += 1;
      return [...current, { id: turnId.current, role, text }];
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
    setState("connecting");
    try {
      const { ws_url } = await api.startVoiceSession();
      const client = new PipecatClient({
        transport: new WebSocketTransport({ serializer: new ProtobufFrameSerializer() }),
        enableCam: false,
        enableMic: true,
        callbacks: {
          onConnected: () => setState("listening"),
          onDisconnected: () => setState("idle"),
          onUserStartedSpeaking: () => setState("listening"),
          onBotStartedSpeaking: () => setState("speaking"),
          onBotStoppedSpeaking: () => setState("listening"),
          onUserTranscript: (data) => {
            if (!data.final || !data.text.trim()) return;
            appendTurn("caller", data.text.trim());
          },
          onBotLlmText: (data) => {
            if (!data.text.trim()) return;
            appendTurn("agent", data.text, true);
          },
          onError: (message) => {
            setError(`Voice session failed (${message.type})`);
            setState("error");
          },
        },
      });
      clientRef.current = client;
      await client.initDevices();
      await client.connect({ wsUrl: ws_url });
    } catch (cause) {
      clientRef.current = null;
      setError(cause instanceof Error ? cause.message : String(cause));
      setState("error");
    }
  };

  const active = state === "connecting" || state === "listening" || state === "speaking";
  return (
    <div className="min-h-screen bg-background text-foreground">
      <StudioHeader active="voice" />
      <main className="mx-auto max-w-5xl px-5 py-8 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="font-display text-2xl tracking-tight text-ink">Conversational agent</h1>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              Speak German through your browser. This generic test exercises the production
              STT–LLM–TTS pipeline but deliberately does not write to a calendar.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void (active ? stop() : start())}
            disabled={state === "connecting"}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {state === "connecting" ? "Connecting…" : active ? "End conversation" : "Start conversation"}
          </button>
        </div>

        {error && <div className="mt-6"><Notice tone="error">{error}</Notice></div>}

        <div className="mt-8 grid gap-5 lg:grid-cols-[240px_1fr]">
          <aside className="rounded-xl border border-border bg-surface p-5">
            <SectionTitle>Session</SectionTitle>
            <div className="mt-4 flex items-center gap-3">
              <span className={`h-3 w-3 rounded-full ${state === "speaking" ? "bg-warning" : active ? "bg-success" : "bg-border"}`} />
              <span className="font-mono text-xs uppercase text-muted-foreground">{state}</span>
            </div>
            <dl className="mt-6 space-y-3 font-mono text-xs text-muted-foreground">
              <div><dt>Transport</dt><dd className="text-ink">WebSocket test</dd></div>
              <div><dt>STT</dt><dd className="text-ink">Deepgram Flux</dd></div>
              <div><dt>LLM</dt><dd className="text-ink">GPT-4.1 mini</dd></div>
              <div><dt>TTS</dt><dd className="text-ink">ElevenLabs Flash</dd></div>
            </dl>
          </aside>

          <section className="min-h-[420px] rounded-xl border border-border bg-surface p-5">
            <SectionTitle>Transcript</SectionTitle>
            {turns.length === 0 ? (
              <p className="mt-6 text-sm text-muted-foreground">
                Start a conversation and allow microphone access. The agent will greet you.
              </p>
            ) : (
              <div className="mt-5 space-y-4" aria-live="polite">
                {turns.map((turn) => (
                  <div key={turn.id} className={turn.role === "agent" ? "pr-8" : "pl-8"}>
                    <div className="font-mono text-[10px] uppercase text-muted-foreground">
                      {turn.role}
                    </div>
                    <p className={`mt-1 rounded-lg px-3 py-2 text-sm ${turn.role === "agent" ? "bg-muted" : "bg-primary/10"}`}>
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
