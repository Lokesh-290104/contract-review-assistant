import { useCallback, useEffect, useRef, useState } from "react";
import { Contract, formatDate, formatSize, MAX_UPLOAD_MB, useApi } from "../api";
import StatusBadge from "./StatusBadge";

const ACCEPT = [".pdf", ".docx", ".txt"];

export function checkFile(file: File): string | null {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ACCEPT.includes(ext)) return "Only PDF, DOCX and TXT files are supported.";
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `The file is larger than ${MAX_UPLOAD_MB} MB.`;
  return null;
}

export default function ContractList() {
  const api = useApi();
  const [contracts, setContracts] = useState<Contract[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setContracts(await api.listContracts());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [api]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while any contract is still processing.
  const processing = contracts?.some((c) => c.status === "processing");
  useEffect(() => {
    if (!processing) return;
    const t = window.setInterval(load, 3000);
    return () => window.clearInterval(t);
  }, [processing, load]);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    const problem = checkFile(file);
    if (problem) {
      setError(problem);
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const created = await api.upload(file);
      setContracts((prev) => [created, ...(prev || [])]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function handleDelete(c: Contract) {
    if (!window.confirm(`Delete "${c.filename}" with its review and chat? This cannot be undone.`)) return;
    try {
      await api.remove(c.id);
      setContracts((prev) => (prev || []).filter((x) => x.id !== c.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section>
      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFile(e.dataTransfer.files[0]);
        }}
      >
        <p className="dropzone-title">{uploading ? "Uploading…" : "Drop a contract here"}</p>
        <p className="muted">PDF, DOCX or TXT, up to {MAX_UPLOAD_MB} MB</p>
        <button className="btn primary" disabled={uploading} onClick={() => inputRef.current?.click()}>
          {uploading ? "Uploading…" : "Choose file"}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT.join(",")}
          hidden
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
      </div>

      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}

      <h2 className="section-title">Your contracts</h2>
      {contracts === null && !error && <div className="skeleton-list" aria-label="Loading" />}
      {contracts?.length === 0 && (
        <div className="empty">No contracts yet. Upload one above to start a review.</div>
      )}
      {contracts && contracts.length > 0 && (
        <div className="table-wrap">
          <table className="contracts">
            <thead>
              <tr>
                <th>Name</th>
                <th>Size</th>
                <th>Uploaded</th>
                <th>Status</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {contracts.map((c) => (
                <tr key={c.id}>
                  <td data-label="Name">
                    <a href={`#/contracts/${c.id}`}>{c.filename}</a>
                    {c.status === "failed" && c.error && <div className="row-error">{c.error}</div>}
                  </td>
                  <td data-label="Size">{formatSize(c.size_bytes)}</td>
                  <td data-label="Uploaded">{formatDate(c.created_at)}</td>
                  <td data-label="Status">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="actions">
                    <a className="btn small" href={`#/contracts/${c.id}`}>
                      Open
                    </a>
                    <button className="btn small danger" onClick={() => handleDelete(c)}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
