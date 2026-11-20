// Thin client for the backend contract (POST /chat, GET /health, GET /sessions/{id}/messages).

declare global {
  interface Window {
    __APP_CONFIG__?: { API_BASE_URL?: string };
  }
}

export type ResolutionSource = "LIVE_INFERENCE" | "LLM_CACHE";

export interface ChatMetadata {
  resolution_source: ResolutionSource;
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  key_id: string | null;
  fallback_attempts: number;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  metadata: ChatMetadata;
}

export interface Health {
  status: string;
  keys_configured: number;
  cache_enabled: boolean;
  failure_simulation_enabled?: boolean;
}

export class ApiError extends Error {
  readonly status: number | null;
  constructor(message: string, status: number | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const REQUEST_TIMEOUT_MS = 60_000;

export function apiBaseUrl(): string {
  const runtime = typeof window !== "undefined" ? window.__APP_CONFIG__?.API_BASE_URL : undefined;
  const base = runtime || import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
  return base.replace(/\/+$/, "");
}

export function newSessionId(): string {
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `s-${random}`;
}

interface ValidationIssue {
  loc?: (string | number)[];
  msg?: string;
}

/** Turn a FastAPI error body into one readable sentence. */
export function describeError(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (status === 503) {
    return "Every API key in the pool is unavailable right now (rate-limited, out of quota or failing). Try again in a moment.";
  }
  if (status === 422) {
    if (Array.isArray(detail) && detail.length > 0) {
      const parts = (detail as ValidationIssue[]).map((issue) => {
        const field = (issue.loc ?? []).filter((p) => p !== "body").join(".");
        return field ? `${field}: ${issue.msg ?? "invalid value"}` : (issue.msg ?? "invalid value");
      });
      return `The server rejected the request. ${parts.join("; ")}.`;
    }
    if (typeof detail === "string") return `The server rejected the request. ${detail}`;
    return "The server rejected the request as invalid.";
  }
  if (typeof detail === "string") return `Request failed (${status}): ${detail}`;
  return `Request failed with status ${status}.`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${apiBaseUrl()}${path}`, { ...init, signal: controller.signal });
  } catch (err) {
    const aborted = err instanceof DOMException && err.name === "AbortError";
    throw new ApiError(
      aborted
        ? "The server took too long to answer. Try again."
        : `Could not reach the server at ${apiBaseUrl()}. Check that the backend is running and allows this origin.`,
      null,
    );
  } finally {
    clearTimeout(timer);
  }

  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) throw new ApiError(describeError(res.status, body), res.status);
  if (body === null) throw new ApiError("The server sent an empty or non-JSON response.", res.status);
  return body as T;
}

export function sendChat(args: {
  sessionId: string;
  message: string;
  systemPrompt: string;
  simulateFailure: string;
}): Promise<ChatResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (args.simulateFailure.trim()) headers["X-Simulate-Failure"] = args.simulateFailure.trim();
  return request<ChatResponse>("/chat", {
    method: "POST",
    headers,
    body: JSON.stringify({
      session_id: args.sessionId,
      message: args.message,
      system_prompt: args.systemPrompt,
    }),
  });
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

export function historyUrl(sessionId: string): string {
  return `${apiBaseUrl()}/sessions/${encodeURIComponent(sessionId)}/messages`;
}
