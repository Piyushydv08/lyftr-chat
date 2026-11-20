import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const health = { status: "ok", keys_configured: 2, cache_enabled: true, failure_simulation_enabled: true };

function reply(source: "LIVE_INFERENCE" | "LLM_CACHE", extra: Record<string, unknown> = {}) {
  return {
    session_id: "ignored",
    reply: "The capital of France is Paris.",
    metadata: {
      resolution_source: source,
      model: "gemini/gemini-2.5-flash-lite",
      latency_ms: source === "LLM_CACHE" ? 4 : 812,
      input_tokens: 42,
      output_tokens: 9,
      key_id: source === "LLM_CACHE" ? null : "key_1",
      fallback_attempts: 0,
      ...extra,
    },
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function routeFetch(chatResponses: Response[]) {
  fetchMock.mockImplementation((url: string) => {
    if (url.endsWith("/health")) return Promise.resolve(json(200, health));
    const next = chatResponses.shift();
    return Promise.resolve(next ?? json(500, { detail: "unexpected" }));
  });
}

describe("chat UI contract", () => {
  it("sends a message, clears the input and renders metadata", async () => {
    routeFetch([json(200, reply("LIVE_INFERENCE"))]);
    const user = userEvent.setup();
    render(<App />);

    const input = screen.getByTestId("chat-input");
    await user.type(input, "What is the capital of France?");
    await user.click(screen.getByTestId("send-button"));
    expect(input).toHaveValue("");

    const msg = await screen.findByTestId("assistant-message");
    expect(msg).toHaveTextContent("The capital of France is Paris.");
    expect(within(msg).getByTestId("meta-source")).toHaveTextContent(/^LIVE_INFERENCE$/);
    expect(within(msg).getByTestId("meta-model")).toHaveTextContent("gemini/gemini-2.5-flash-lite");
    expect(within(msg).getByTestId("meta-latency")).toHaveTextContent("812 ms");
    expect(within(msg).getByTestId("meta-input-tokens")).toHaveTextContent(/^42$/);
    expect(within(msg).getByTestId("meta-output-tokens")).toHaveTextContent(/^9$/);

    const chatCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/chat"))!;
    const body = JSON.parse(chatCall[1].body);
    expect(body.message).toBe("What is the capital of France?");
    expect(body.session_id).toMatch(/^s-/);
  });

  it("sends with Enter and keeps the same session across turns", async () => {
    routeFetch([json(200, reply("LIVE_INFERENCE")), json(200, reply("LLM_CACHE"))]);
    const user = userEvent.setup();
    render(<App />);
    const input = screen.getByTestId("chat-input");
    await user.type(input, "first{Enter}");
    await screen.findByTestId("assistant-message");
    await user.type(input, "second{Enter}");
    await waitFor(() => expect(screen.getAllByTestId("assistant-message")).toHaveLength(2));
    const second = screen.getAllByTestId("assistant-message")[1];
    expect(within(second).getByTestId("meta-source")).toHaveTextContent(/^LLM_CACHE$/);

    const ids = fetchMock.mock.calls
      .filter(([url]) => String(url).endsWith("/chat"))
      .map(([, init]) => JSON.parse(init.body).session_id);
    expect(new Set(ids).size).toBe(1);
  });

  it("shows a readable message on 503 instead of a blank screen", async () => {
    routeFetch([json(503, { detail: "all providers unavailable" })]);
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "hello{Enter}");
    const alert = await screen.findByTestId("error-message");
    expect(alert).toHaveTextContent(/unavailable/i);
    expect(screen.queryByTestId("assistant-message")).toBeNull();
  });

  it("shows a readable message on 422", async () => {
    routeFetch([
      json(422, {
        detail: [{ type: "string_too_long", loc: ["body", "message"], msg: "String should have at most 4000 characters" }],
      }),
    ]);
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "too long{Enter}");
    const alert = await screen.findByTestId("error-message");
    expect(alert).toHaveTextContent("message: String should have at most 4000 characters");
  });

  it("shows a readable message when the backend is unreachable", async () => {
    fetchMock.mockImplementation((url: string) =>
      url.endsWith("/health") ? Promise.resolve(json(200, health)) : Promise.reject(new TypeError("Failed to fetch")),
    );
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "hello{Enter}");
    expect(await screen.findByTestId("error-message")).toHaveTextContent(/Could not reach the server/);
  });

  it("sends the system prompt and the failover header", async () => {
    routeFetch([json(200, reply("LIVE_INFERENCE", { key_id: "key_2", fallback_attempts: 1 }))]);
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("system-prompt-input"), "You are a pirate.");
    await user.selectOptions(screen.getByTestId("simulate-failure-select"), "key_1=429");
    await user.type(screen.getByTestId("chat-input"), "ahoy{Enter}");
    const msg = await screen.findByTestId("assistant-message");
    expect(msg).toHaveTextContent("key_2");

    const [, init] = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/chat"))!;
    expect(init.headers["X-Simulate-Failure"]).toBe("key_1=429");
    expect(JSON.parse(init.body).system_prompt).toBe("You are a pirate.");
  });
});
