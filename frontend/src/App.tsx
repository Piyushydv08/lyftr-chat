import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, getHealth, historyUrl, newSessionId, sendChat, type ChatMetadata, type Health } from "./api";
import { AssistantMessage } from "./components/AssistantMessage";
import { SessionPanel } from "./components/SessionPanel";

const MAX_CHARS = 4000;

type Item =
  | { id: number; kind: "user"; text: string }
  | { id: number; kind: "assistant"; text: string; meta: ChatMetadata }
  | { id: number; kind: "error"; text: string; retryMessage: string };

const SUGGESTIONS = ["What is the capital of France?", "Explain an HTTP 429 error in one sentence."];

let nextId = 1;

export default function App() {
  // A new session starts on every page load (and on "New chat").
  const [sessionId, setSessionId] = useState(newSessionId);
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [failurePreset, setFailurePreset] = useState("");
  const [customFailure, setCustomFailure] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    getHealth()
      .then((h) => {
        setHealth(h);
        setHealthError(null);
      })
      .catch((err: unknown) => setHealthError(err instanceof ApiError ? err.message : "Backend health check failed."));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [items, pending]);

  const simulateFailure = failurePreset === "custom" ? customFailure : failurePreset;

  const send = useCallback(
    async (raw: string) => {
      const message = raw.trim();
      if (!message || pending) return;
      setInput("");
      setItems((prev) => [...prev, { id: nextId++, kind: "user", text: message }]);
      setPending(true);
      try {
        const res = await sendChat({ sessionId, message, systemPrompt, simulateFailure });
        setItems((prev) => [...prev, { id: nextId++, kind: "assistant", text: res.reply, meta: res.metadata }]);
      } catch (err) {
        const text = err instanceof ApiError ? err.message : "Something went wrong while sending the message.";
        setItems((prev) => [...prev, { id: nextId++, kind: "error", text, retryMessage: message }]);
      } finally {
        setPending(false);
        inputRef.current?.focus();
      }
    },
    [pending, sessionId, systemPrompt, simulateFailure],
  );

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(input);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(input);
    }
  };

  const newChat = () => {
    setSessionId(newSessionId());
    setItems([]);
    setInput("");
    inputRef.current?.focus();
  };

  const charCount = input.trim().length;
  const overLimit = charCount > MAX_CHARS;
  const hasHistory = items.some((i) => i.kind === "assistant");

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1 className="brand-name">Lyftr Chat</h1>
          <p className="brand-tagline">Every reply shows the route it took through the key pool.</p>
        </div>
        {simulateFailure && (
          <p className="sim-flag" role="status">
            Failover test on: <code>{simulateFailure}</code>
          </p>
        )}
      </header>

      <div className="layout">
        <main className="chat" aria-label="Conversation">
          <div className="stream" aria-live="polite">
            <div className="stream-inner">
            {items.length === 0 && !pending && (
              <section className="empty">
                <h2 className="empty-title">Start a conversation</h2>
                <p className="empty-text">
                  Under each reply you will see whether it came from a live model call or the response cache, which API key
                  served it, how long it took and how many tokens that request used.
                </p>
                <div className="suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} type="button" className="suggestion" onClick={() => void send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </section>
            )}

            {items.map((item) => {
              if (item.kind === "user") {
                return (
                  <div key={item.id} className="turn turn--user" data-testid="user-message">
                    <p className="reply">{item.text}</p>
                  </div>
                );
              }
              if (item.kind === "assistant") {
                return <AssistantMessage key={item.id} text={item.text} meta={item.meta} />;
              }
              return (
                <div key={item.id} className="turn turn--error" role="alert" data-testid="error-message">
                  <p className="error-text">{item.text}</p>
                  <button
                    type="button"
                    className="button button--quiet"
                    disabled={pending}
                    onClick={() => void send(item.retryMessage)}
                  >
                    Retry message
                  </button>
                </div>
              );
            })}

            {pending && (
              <div className="turn turn--pending" aria-label="Waiting for the reply">
                <span className="pulse" aria-hidden="true" />
                Waiting for the gateway
              </div>
            )}
              <div ref={endRef} />
            </div>
          </div>

          <form className="composer" onSubmit={onSubmit}>
            <label htmlFor="chat-input" className="visually-hidden">
              Message
            </label>
            <textarea
              id="chat-input"
              ref={inputRef}
              data-testid="chat-input"
              className="composer-input"
              rows={2}
              placeholder="Message the assistant (Enter to send, Shift+Enter for a new line)"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              autoFocus
            />
            <div className="composer-bar">
              <span className={`counter ${overLimit ? "counter--over" : ""}`}>
                {charCount} / {MAX_CHARS}
              </span>
              <button
                type="submit"
                data-testid="send-button"
                className="button button--primary"
                disabled={pending || charCount === 0}
              >
                {pending ? "Sending" : "Send"}
              </button>
            </div>
          </form>
        </main>

        <SessionPanel
          sessionId={sessionId}
          hasHistory={hasHistory}
          historyHref={historyUrl(sessionId)}
          onNewChat={newChat}
          systemPrompt={systemPrompt}
          onSystemPromptChange={setSystemPrompt}
          failurePreset={failurePreset}
          onFailurePresetChange={setFailurePreset}
          customFailure={customFailure}
          onCustomFailureChange={setCustomFailure}
          health={health}
          healthError={healthError}
        />
      </div>
    </div>
  );
}
