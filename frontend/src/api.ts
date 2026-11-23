// Typed API client. Every call attaches the Clerk session token; the backend verifies it.
import { useAuth } from "@clerk/react";
import { useCallback, useMemo } from "react";

export type ContractStatus = "processing" | "reviewed" | "failed";
export type FindingStatus = "found" | "not_found" | "unclear";

export interface Contract {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: ContractStatus;
  error: string | null;
  created_at: string;
  reviewed_at: string | null;
}

export interface Finding {
  item_key: string;
  label: string;
  status: FindingStatus;
  summary: string;
  quote: string | null;
  quote_start: number | null;
  quote_end: number | null;
  section: string | null;
  page: number | null;
  chunk_id: number | null;
  verified: boolean;
  note: string | null;
  risk: string | null;
}

export interface Clause {
  id: number;
  idx: number;
  section: string | null;
  heading: string | null;
  page_start: number | null;
  page_end: number | null;
  start_char: number;
  end_char: number;
  text: string;
}

export interface ContractDetail extends Contract {
  has_pages: boolean;
  findings: Finding[];
  clauses: Clause[];
}

export interface Citation {
  quote: string;
  verified: boolean;
  section?: string | null;
  page?: number | null;
  chunk_id?: number | null;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  citations: Citation[] | null;
  created_at: string;
}

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

export const MAX_UPLOAD_MB = 20;

async function readError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status}).`;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") message = body.detail;
    else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join(", ");
  } catch {
    /* not JSON */
  }
  if (res.status === 401) message = "Your session expired. Please sign in again.";
  return new ApiError(message, res.status);
}

export interface StreamHandlers {
  onToken: (text: string) => void;
  onDone: (message: ChatMessage) => void;
  onError: (message: string, rateLimited: boolean) => void;
}

export function useApi() {
  const { getToken } = useAuth();

  const request = useCallback(
    async (path: string, init: RequestInit = {}): Promise<Response> => {
      const token = await getToken();
      let res: Response;
      try {
        res = await fetch(path, {
          ...init,
          headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` },
        });
      } catch {
        throw new ApiError("Cannot reach the server. Is the backend running?", 0);
      }
      if (!res.ok) throw await readError(res);
      return res;
    },
    [getToken],
  );

  return useMemo(
    () => ({
      listContracts: async (): Promise<Contract[]> => (await request("/api/contracts")).json(),
      getContract: async (id: string): Promise<ContractDetail> => (await request(`/api/contracts/${id}`)).json(),
      upload: async (file: File): Promise<Contract> => {
        const form = new FormData();
        form.append("file", file);
        return (await request("/api/contracts", { method: "POST", body: form })).json();
      },
      remove: async (id: string): Promise<void> => {
        await request(`/api/contracts/${id}`, { method: "DELETE" });
      },
      retry: async (id: string): Promise<Contract> =>
        (await request(`/api/contracts/${id}/review`, { method: "POST" })).json(),
      messages: async (id: string): Promise<ChatMessage[]> =>
        (await request(`/api/contracts/${id}/messages`)).json(),

      /** POST a question and read the Server-Sent Events stream as it arrives. */
      ask: async (id: string, question: string, h: StreamHandlers, signal?: AbortSignal): Promise<void> => {
        let res: Response;
        try {
          res = await request(`/api/contracts/${id}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question }),
            signal,
          });
        } catch (e) {
          h.onError(e instanceof Error ? e.message : "Request failed.", false);
          return;
        }
        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let finished = false;
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let cut: number;
          while ((cut = buffer.indexOf("\n\n")) !== -1) {
            const block = buffer.slice(0, cut);
            buffer = buffer.slice(cut + 2);
            let event = "message";
            let data = "";
            for (const line of block.split("\n")) {
              if (line.startsWith("event: ")) event = line.slice(7);
              else if (line.startsWith("data: ")) data += line.slice(6);
            }
            if (!data) continue;
            const payload = JSON.parse(data);
            if (event === "token") h.onToken(payload.t);
            else if (event === "done") {
              finished = true;
              h.onDone(payload as ChatMessage);
            } else if (event === "error") {
              finished = true;
              h.onError(payload.message, Boolean(payload.rate_limited));
            }
          }
        }
        if (!finished) h.onError("The answer stopped before it finished. Please try again.", false);
      },
    }),
    [request],
  );
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function locationLabel(section: string | null | undefined, page: number | null | undefined): string {
  const parts: string[] = [];
  if (section) parts.push(/^(article|preamble|[a-z])/i.test(section) ? section : `Section ${section}`);
  if (page) parts.push(`page ${page}`);
  return parts.join(", ") || "location unknown";
}
