import { useEffect, useState } from "react";
import type { Health } from "../api";

export const FAILURE_PRESETS: { value: string; label: string }[] = [
  { value: "", label: "Off: normal requests" },
  { value: "key_1=429", label: "key_1 returns 429" },
  { value: "key_1=500", label: "key_1 returns 500" },
  { value: "key_1=503", label: "key_1 returns 503" },
  { value: "*=429", label: "Every key returns 429" },
  { value: "custom", label: "Custom header value" },
];

interface Props {
  sessionId: string;
  hasHistory: boolean;
  historyHref: string;
  onNewChat: () => void;
  systemPrompt: string;
  onSystemPromptChange: (v: string) => void;
  failurePreset: string;
  onFailurePresetChange: (v: string) => void;
  customFailure: string;
  onCustomFailureChange: (v: string) => void;
  health: Health | null;
  healthError: string | null;
}

const SMALL_SCREEN = "(max-width: 860px)";

function useIsSmallScreen(): boolean {
  const query = typeof window !== "undefined" && window.matchMedia ? window.matchMedia(SMALL_SCREEN) : null;
  const [small, setSmall] = useState(query?.matches ?? false);
  useEffect(() => {
    if (!query) return;
    const onChange = (e: MediaQueryListEvent) => setSmall(e.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [query]);
  return small;
}

export function SessionPanel(props: Props) {
  const simulationDisabled = props.health?.failure_simulation_enabled === false;
  const small = useIsSmallScreen();
  const [openOnSmall, setOpenOnSmall] = useState(false);
  const open = !small || openOnSmall;
  return (
    <aside className="panel" aria-label="Session settings">
      <details
        className="panel-details"
        open={open}
        onToggle={(e) => small && setOpenOnSmall((e.target as HTMLDetailsElement).open)}
      >
        <summary className="panel-summary">Session settings</summary>
        <section className="panel-section">
          <h2 className="panel-title">Session</h2>
          <p className="session-id" title={props.sessionId}>
            {props.sessionId}
          </p>
          <div className="panel-actions">
            <button type="button" className="button button--quiet" onClick={props.onNewChat}>
              New chat
            </button>
            {props.hasHistory && (
              <a className="link" href={props.historyHref} target="_blank" rel="noreferrer">
                Open stored history
              </a>
            )}
          </div>
        </section>

        <section className="panel-section">
          <label className="panel-title" htmlFor="system-prompt">
            System prompt
          </label>
          <textarea
            id="system-prompt"
            data-testid="system-prompt-input"
            className="field field--area"
            rows={4}
            maxLength={8000}
            placeholder="You are a helpful, concise assistant."
            value={props.systemPrompt}
            onChange={(e) => props.onSystemPromptChange(e.target.value)}
          />
          <p className="hint">Used from your next message in this session. Leave empty for the server default.</p>
        </section>

        <section className="panel-section">
          <label className="panel-title" htmlFor="failure-preset">
            Failover test
          </label>
          <select
            id="failure-preset"
            data-testid="simulate-failure-select"
            className="field"
            value={props.failurePreset}
            onChange={(e) => props.onFailurePresetChange(e.target.value)}
          >
            {FAILURE_PRESETS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          {props.failurePreset === "custom" && (
            <input
              aria-label="Custom X-Simulate-Failure value"
              data-testid="simulate-failure-input"
              className="field"
              placeholder="key_1=429,key_2=500"
              value={props.customFailure}
              onChange={(e) => props.onCustomFailureChange(e.target.value)}
            />
          )}
          <p className="hint">
            Sends the X-Simulate-Failure header with each message so you can watch the gateway fail over. Simulated requests
            skip the cache lookup.
          </p>
          {simulationDisabled && (
            <p className="hint hint--warn">This server has failure simulation turned off, so the header is ignored.</p>
          )}
        </section>

        <section className="panel-section">
          <h2 className="panel-title">Gateway</h2>
          {props.healthError ? (
            <p className="status status--down">{props.healthError}</p>
          ) : props.health ? (
            <ul className="status-list">
              <li>{props.health.keys_configured} API keys in the pool</li>
              <li>Response cache {props.health.cache_enabled ? "on" : "off"}</li>
            </ul>
          ) : (
            <p className="hint">Checking the backend…</p>
          )}
        </section>
      </details>
    </aside>
  );
}
