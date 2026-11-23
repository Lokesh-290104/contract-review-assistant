import { useCallback, useEffect, useState } from "react";
import { ContractDetail, Finding, formatDate, formatSize, locationLabel, useApi } from "../api";
import Chat from "./Chat";
import ClauseViewer, { Highlight } from "./ClauseViewer";
import StatusBadge from "./StatusBadge";

type Tab = "contract" | "chat";

export default function ContractView({ id }: { id: string }) {
  const api = useApi();
  const [contract, setContract] = useState<ContractDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [tab, setTab] = useState<Tab>("contract");
  const [highlight, setHighlight] = useState<Highlight | null>(null);

  const load = useCallback(async () => {
    try {
      setContract(await api.getContract(id));
      setError(null);
    } catch (e) {
      const err = e as Error & { status?: number };
      if (err.status === 404) setNotFound(true);
      else setError(err.message);
    }
  }, [api, id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (contract?.status !== "processing") return;
    const t = window.setInterval(load, 3000);
    return () => window.clearInterval(t);
  }, [contract?.status, load]);

  function jumpTo(chunkId: number | null | undefined, quote: string | null | undefined, start?: number | null) {
    if (!chunkId) return;
    setHighlight({ chunkId, quote: quote || null, start: start ?? null });
    setTab("contract");
  }

  async function retry() {
    try {
      await api.retry(id);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove() {
    if (!contract || !window.confirm(`Delete "${contract.filename}" with its review and chat?`)) return;
    try {
      await api.remove(id);
      window.location.hash = "#/";
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (notFound)
    return (
      <div className="empty">
        This contract does not exist or is not yours. <a href="#/">Back to your contracts</a>
      </div>
    );
  if (!contract)
    return error ? <div className="alert error">{error}</div> : <div className="skeleton-list" aria-label="Loading" />;

  return (
    <section className="contract-view">
      <a href="#/" className="back">
        ← All contracts
      </a>
      <div className="contract-header">
        <div>
          <h1>{contract.filename}</h1>
          <p className="muted">
            {formatSize(contract.size_bytes)} · uploaded {formatDate(contract.created_at)}
          </p>
        </div>
        <div className="header-actions">
          <StatusBadge status={contract.status} />
          {contract.status === "failed" && (
            <button className="btn primary small" onClick={retry}>
              Retry review
            </button>
          )}
          <button className="btn small danger" onClick={remove}>
            Delete
          </button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {contract.status === "failed" && <div className="alert error">{contract.error}</div>}

      <div className="split">
        <div className="panel">
          <h2 className="panel-title">Checklist</h2>
          {contract.status === "processing" && (
            <div className="processing-note">
              <span className="spinner" /> Reading the contract and checking each item. This usually takes under a
              minute.
            </div>
          )}
          {contract.findings.map((f) => (
            <FindingCard key={f.item_key} f={f} onOpen={() => jumpTo(f.chunk_id, f.quote, f.quote_start)} />
          ))}
          {contract.status !== "processing" && contract.findings.length === 0 && (
            <div className="empty small">No results yet.</div>
          )}
        </div>

        <div className="panel">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "contract"} className={tab === "contract" ? "active" : ""} onClick={() => setTab("contract")}>
              Contract text
            </button>
            <button role="tab" aria-selected={tab === "chat"} className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
              Ask a question
            </button>
          </div>
          {/* Both stay mounted so chat keeps streaming while you look at a clause. */}
          <div hidden={tab !== "contract"}>
            <ClauseViewer clauses={contract.clauses} highlight={highlight} />
          </div>
          <div hidden={tab !== "chat"}>
            <Chat contractId={id} enabled={contract.status === "reviewed"} onJump={(c) => jumpTo(c.chunk_id, c.quote)} />
          </div>
        </div>
      </div>
    </section>
  );
}

function FindingCard({ f, onOpen }: { f: Finding; onOpen: () => void }) {
  return (
    <article className={`finding finding-${f.status}`}>
      <header>
        <h3>{f.label}</h3>
        <StatusBadge status={f.status} />
      </header>
      {f.status === "not_found" ? (
        <p className="summary muted">Not found in the contract.</p>
      ) : (
        <p className="summary">{f.summary}</p>
      )}
      {f.quote && f.verified && (
        <button className="quote" onClick={onOpen} title="Show the full clause">
          <span className="quote-text">“{f.quote}”</span>
          <span className="quote-meta">
            {locationLabel(f.section, f.page)} · ✓ matches contract text · show full clause →
          </span>
        </button>
      )}
      {f.note && <p className="note">{f.note}</p>}
      {f.risk && <p className="risk">⚠ Needs attention: {f.risk}</p>}
    </article>
  );
}
