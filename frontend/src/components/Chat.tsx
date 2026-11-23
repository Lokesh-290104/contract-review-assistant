import { FormEvent, useEffect, useRef, useState } from "react";
import { ChatMessage, Citation, locationLabel, useApi } from "../api";

interface Props {
  contractId: string;
  enabled: boolean;
  onJump: (c: Citation) => void;
}

export default function Chat({ contractId, enabled, onJump }: Props) {
  const api = useApi();
  const [messages, setMessages] = useState<ChatMessage[] | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    api
      .messages(contractId)
      .then(setMessages)
      .catch((e: Error) => setError(e.message));
    return () => abortRef.current?.abort();
  }, [api, contractId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, draft, pendingQuestion]);

  async function send(e: FormEvent) {
    e.preventDefault();
    const question = input.trim();
    if (!question || streaming) return;
    setInput("");
    setError(null);
    setPendingQuestion(question);
    setDraft("");
    setStreaming(true);
    abortRef.current = new AbortController();
    let text = "";
    await api.ask(
      contractId,
      question,
      {
        onToken: (t) => {
          text += t;
          setDraft(text);
        },
        onDone: (answer) => {
          const asked: ChatMessage = {
            id: answer.id - 0.5,
            role: "user",
            content: question,
            citations: null,
            created_at: answer.created_at,
          };
          setMessages((prev) => [...(prev || []), asked, answer]);
        },
        onError: (message, rateLimited) => {
          setError(rateLimited ? `⏳ ${message}` : message);
          setInput(question); // let the user resend
        },
      },
      abortRef.current.signal,
    ).catch((err: Error) => {
      if (err.name !== "AbortError") setError(err.message);
    });
    setPendingQuestion(null);
    setDraft("");
    setStreaming(false);
  }

  if (!enabled)
    return <div className="empty small">You can ask questions once the review has finished.</div>;

  return (
    <div className="chat">
      <div className="chat-log" aria-live="polite">
        {messages === null && !error && <div className="muted small">Loading chat…</div>}
        {messages?.length === 0 && !pendingQuestion && (
          <div className="empty small">
            Ask anything about this contract, e.g. “Can we end it early?” Answers quote the contract and say so when the
            contract is silent.
          </div>
        )}
        {messages?.map((m) => <Bubble key={m.id} m={m} onJump={onJump} />)}
        {pendingQuestion && (
          <>
            <div className="bubble user">{pendingQuestion}</div>
            <div className="bubble assistant">
              {draft || <span className="typing">Reading the contract…</span>}
              {draft && <span className="cursor" aria-hidden />}
            </div>
          </>
        )}
        <div ref={bottomRef} />
      </div>
      {error && <div className="alert error">{error}</div>}
      <form className="chat-input" onSubmit={send}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about this contract…"
          maxLength={2000}
          disabled={streaming}
          aria-label="Your question"
        />
        <button className="btn primary" disabled={streaming || !input.trim()}>
          {streaming ? "Answering…" : "Ask"}
        </button>
      </form>
    </div>
  );
}

function Bubble({ m, onJump }: { m: ChatMessage; onJump: (c: Citation) => void }) {
  return (
    <div className={`bubble ${m.role}`}>
      <div className="bubble-text">{m.content}</div>
      {m.citations && m.citations.length > 0 && (
        <div className="citations">
          {m.citations.map((c, i) =>
            c.verified ? (
              <button key={i} className="citation" onClick={() => onJump(c)} title={c.quote}>
                ✓ {locationLabel(c.section, c.page)} →
              </button>
            ) : (
              <span key={i} className="citation unverified" title={c.quote}>
                ✗ quote not found in contract
              </span>
            ),
          )}
        </div>
      )}
    </div>
  );
}
