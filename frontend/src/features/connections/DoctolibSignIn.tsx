"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ConnectionSummary, DoctolibLoginStatus } from "@/types";

/** "Sign in again" for a doctolib connection: one click, with the login the server stores.
 *
 *  The same connection is refreshed, so nothing bound to it has to change. When doctolib e-mails
 *  a code, a field for it appears here. When it turns the password or PIN down — or the practice
 *  changed them — a short form takes the new one; the email stays the account's. */
export function DoctolibSignIn({
  connection,
  onSignedIn,
}: {
  connection: ConnectionSummary;
  onSignedIn?: () => void;
}) {
  const [job, setJob] = useState<DoctolibLoginStatus | null>(null);
  const [code, setCode] = useState("");
  const [changing, setChanging] = useState(false);
  const [password, setPassword] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  // Kept in a ref, so a parent passing a new function each render does not restart the polling.
  const signedInRef = useRef(onSignedIn);
  signedInRef.current = onSignedIn;

  const running = job !== null && job.status !== "awaiting_code";
  const awaitingCode = job?.status === "awaiting_code";

  // Follow the login on the server until it settles.
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return;
    const timer = setTimeout(() => {
      api
        .pollDoctolibLogin(job.job_id)
        .then((next) => {
          if (next.status === "done") {
            setJob(null);
            setCode("");
            setPassword("");
            setPin("");
            setChanging(false);
            setDone(true);
            signedInRef.current?.();
          } else if (next.status === "failed") {
            setJob(null);
            setCode("");
            if (next.refused) setChanging(true);
            setError(
              next.refused === "password"
                ? "Doctolib did not accept the password. Enter the current one."
                : next.refused === "pin"
                  ? "Doctolib asks for the PIN, and the stored one does not work. Enter the current PIN."
                  : next.error || "The sign-in did not complete. Try again.",
            );
          } else {
            setJob(next);
          }
        })
        .catch((e) => {
          setJob(null);
          setError(e instanceof Error ? e.message : String(e));
        });
    }, 2000);
    return () => clearTimeout(timer);
  }, [job]);

  async function signIn(details: { password?: string; pin?: string } = {}) {
    setError("");
    setDone(false);
    try {
      setJob(await api.signInDoctolibAgain(connection.id, details));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function sendCode() {
    if (!job) return;
    setError("");
    try {
      setJob(await api.submitDoctolibCode(job.job_id, code.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const signedIn = connection.signed_in_at ?? connection.created_at;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <button
          type="button"
          className="studio-primary"
          disabled={running || awaitingCode}
          onClick={() => void signIn()}
        >
          {running ? "Signing in to Doctolib…" : "Sign in again"}
        </button>
        {!changing && !awaitingCode && (
          <button
            type="button"
            className="text-sm text-primary hover:underline disabled:opacity-50"
            disabled={running}
            onClick={() => setChanging(true)}
          >
            Login details changed?
          </button>
        )}
        <span className="text-xs text-muted-foreground">
          {done
            ? "Signed in just now."
            : `Last sign-in ${new Date(signedIn).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}`}
        </span>
      </div>
      {running && (
        <p className="text-xs text-muted-foreground">
          The server signs in with the stored email, password and PIN. This takes about a minute.
        </p>
      )}

      {awaitingCode && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void sendCode();
          }}
        >
          <label className="grid gap-1 text-xs text-muted-foreground">
            Doctolib e-mailed a 6-digit code. Enter it here:
            <input
              autoFocus
              required
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              className="studio-input w-40"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            />
          </label>
          <button type="submit" className="studio-primary" disabled={code.length < 6}>
            Confirm code
          </button>
        </form>
      )}

      {changing && !awaitingCode && (
        <form
          className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-[1fr_10rem_auto] sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void signIn({ password, pin });
          }}
        >
          <label className="grid gap-1 text-xs text-muted-foreground">
            New password (blank keeps the stored one)
            <input
              type="password"
              autoComplete="current-password"
              className="studio-input"
              value={password}
              disabled={running}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <label className="grid gap-1 text-xs text-muted-foreground">
            New PIN (4 digits)
            <input
              type="password"
              inputMode="numeric"
              maxLength={4}
              autoComplete="off"
              className="studio-input"
              value={pin}
              disabled={running}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
            />
          </label>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              className="studio-primary"
              disabled={running || (!password && !pin) || (pin.length > 0 && pin.length !== 4)}
            >
              Sign in
            </button>
            <button
              type="button"
              className="text-sm text-muted-foreground"
              onClick={() => {
                setChanging(false);
                setPassword("");
                setPin("");
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {error && (
        <p className="rounded-md bg-danger/10 p-3 text-sm text-danger" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
