import type { ChatMetadata } from "../api";

/** Which keys the request touched: failed keys in order, then the key (or cache) that answered. */
function RouteLine({ meta }: { meta: ChatMetadata }) {
  if (meta.resolution_source === "LLM_CACHE") {
    return (
      <ol className="route" aria-label="How this reply was served">
        <li className="route-node route-node--cache">Response cache</li>
        <li className="route-note">no provider call, no key used</li>
      </ol>
    );
  }
  const failed = Array.from({ length: meta.fallback_attempts }, (_, i) => `key_${i + 1}`);
  return (
    <ol className="route" aria-label="How this reply was served">
      {failed.map((key) => (
        <li key={key} className="route-node route-node--failed">
          <span className="visually-hidden">failed: </span>
          <span className="route-label">{key}</span>
        </li>
      ))}
      <li className="route-node route-node--live">
        <span className="visually-hidden">answered by: </span>
        {meta.key_id ?? "unknown key"}
      </li>
      {meta.fallback_attempts > 0 && (
        <li className="route-note">
          failed over after {meta.fallback_attempts} {meta.fallback_attempts === 1 ? "key" : "keys"}
        </li>
      )}
    </ol>
  );
}

export function AssistantMessage({ text, meta }: { text: string; meta: ChatMetadata }) {
  const isCache = meta.resolution_source === "LLM_CACHE";
  return (
    <article className={`turn turn--assistant ${isCache ? "is-cache" : "is-live"}`} data-testid="assistant-message">
      <p className="reply">{text}</p>
      <RouteLine meta={meta} />
      <dl className="meta">
        <div className="meta-item">
          <dt>Source</dt>
          <dd>
            <span className={`source-badge ${isCache ? "source-badge--cache" : "source-badge--live"}`} data-testid="meta-source">
              {meta.resolution_source}
            </span>
          </dd>
        </div>
        <div className="meta-item">
          <dt>Model</dt>
          <dd data-testid="meta-model">{meta.model}</dd>
        </div>
        <div className="meta-item">
          <dt>Latency</dt>
          <dd data-testid="meta-latency">{meta.latency_ms} ms</dd>
        </div>
        <div className="meta-item">
          <dt>Input tokens</dt>
          <dd data-testid="meta-input-tokens">{meta.input_tokens}</dd>
        </div>
        <div className="meta-item">
          <dt>Output tokens</dt>
          <dd data-testid="meta-output-tokens">{meta.output_tokens}</dd>
        </div>
      </dl>
    </article>
  );
}
