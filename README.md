# Contract Review Assistant

A full-stack web app. A signed-in user uploads a contract (PDF, DOCX or TXT, up to 20 MB) and the app checks it
against a fixed checklist: **parties, start date and term, payment terms, termination, limitation of liability,
confidentiality, governing law, auto-renewal**.

For every item the app shows a status (**found / not found / unclear**), a one-line plain-English summary, and the
**clause quoted word for word** with its section and page. Click the quote to see the whole clause. The backend checks
every quote against the contract text, and any item whose quote it cannot find is marked **unclear**. A follow-up
chat answers questions from the contract only, quotes the clause, and streams the answer as it is written.

---

## Contents
1. [Quick start (Docker)](#1-quick-start-docker)
2. [Setup: Clerk and AI keys](#2-setup-clerk-and-ai-keys)
3. [Run without Docker](#3-run-without-docker)
4. [Tests and evaluation](#4-tests-and-evaluation)
5. [Stack and why](#5-stack-and-why)
6. [Architecture](#6-architecture)
7. [Chunking and retrieval, per checklist item](#7-chunking-and-retrieval-per-checklist-item)
8. [How quotes are checked](#8-how-quotes-are-checked)
9. [How each user's data is kept separate](#9-how-each-users-data-is-kept-separate)
10. [API](#10-api)
11. [Assumptions](#11-assumptions)
12. [Known limitations and what I would improve](#12-known-limitations-and-what-i-would-improve)
13. [How I used AI tools](#13-how-i-used-ai-tools)

---

## 1. Quick start (Docker)

Needs Docker Desktop.

```bash
cp .env.example .env        # then fill in the 3 keys (section 2)
docker compose up --build
```

Open **http://localhost:5173**. Compose starts three containers:

| Service | What it is | Port |
|---|---|---|
| `db` | PostgreSQL 16 + pgvector | internal |
| `api` | FastAPI backend | 8000 |
| `web` | React app served by nginx, which also proxies `/api` (with SSE buffering off) | 5173 |

The tables are created when the API starts.

## 2. Setup: Clerk and AI keys

All secrets go in **`.env`** in the repo root. It is git-ignored. Only `.env.example` is committed.

### Create the Clerk application (free plan)
1. Sign up at <https://dashboard.clerk.com> and click **Create application**.
2. Pick a name, turn on **Email** (and any social logins you like), then click **Create**.
3. Open **Configure → API keys**:
   - copy the **Publishable key** (`pk_test_…`) into `VITE_CLERK_PUBLISHABLE_KEY`;
   - copy the **Secret key** (`sk_test_…`) into `CLERK_SECRET_KEY`.
4. Keep `CLERK_AUTHORIZED_PARTIES=http://localhost:5173`. If you serve the frontend from another origin, add it here,
   comma-separated.

### Create the Gemini key (default AI provider, free tier)
1. Open <https://aistudio.google.com/apikey> and sign in with a Google account.
2. Click **Create API key** (let it create a project if asked) and copy the key.
3. Put it in `GEMINI_API_KEY`. Keep `AI_PROVIDER=gemini` and the defaults:
   - `CHAT_MODEL=gemini-3.5-flash-lite`
   - `EMBED_MODEL=gemini-embedding-001`, `EMBED_DIM=1536`

### Or use OpenRouter (the brief's suggestion)
1. Sign up at <https://openrouter.ai>, then open **Keys → Create key**, and put it in `OPENROUTER_API_KEY`.
2. Set `AI_PROVIDER=openrouter`, `CHAT_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free`,
   `EMBED_MODEL=nvidia/nemotron-3.5-embed-1b:free` and `EMBED_DIM=2048`.
   (Check the current free list at <https://openrouter.ai/models?q=free>.)

`EMBED_DIM` must match the database. When you change it, start with an empty database (`docker compose down -v`, or
delete `.localdb` for the local run).

### Which key goes where

| Key | Used by | Why |
|---|---|---|
| `VITE_CLERK_PUBLISHABLE_KEY` | frontend (build time) | Clerk's sign-in UI and session tokens |
| `CLERK_SECRET_KEY` | backend | to download Clerk's public signing keys (JWKS) and verify tokens |
| `CLERK_AUTHORIZED_PARTIES` | backend | token's `azp` must be one of these origins |
| `GEMINI_API_KEY` (or `OPENROUTER_API_KEY`) | backend only | chat + embeddings. It never reaches the browser |

### Why Gemini instead of OpenRouter (the brief says to tell you why)
I built and tested the app on OpenRouter's suggested free models first (eval: 14/16). The OpenRouter free tier allows
**50 free-model requests per day** per account, and one contract review uses about 10. Testing used up the day's
limit, which would also block a reviewer or a demo. Gemini's free tier has its own, larger per-model quota (see
AI Studio → Usage & limits), and `gemini-3.5-flash-lite` answered review prompts in about 1 s. Both providers speak
the OpenAI-compatible API, so the switch is configuration (`llm.PROVIDERS`), and OpenRouter still works with
`AI_PROVIDER=openrouter`.

Note: on Google's free tier, Google may use prompts to improve its products. That is fine for sample contracts. For
real client contracts, use a paid tier or another provider.


## 3. Run without Docker

Windows / macOS / Linux with Python 3.12 and Node 20+. Postgres and pgvector come from the
[`pgserver`](https://pypi.org/project/pgserver/) pip package, so there is nothing else to install.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements-dev.txt      # macOS/Linux: .venv/bin/python
cd frontend && npm install && cd ..
.venv/Scripts/python scripts/run_local.py
```

Open **http://localhost:5173**. Vite proxies `/api` to the API on port 8000. The data is kept in `./.localdb`.
Press **Ctrl+C** in the same window to stop everything (web app, API and Postgres).

## 4. Tests and evaluation

```bash
cd backend && ../.venv/Scripts/python -m pytest      # 67 tests, offline
cd frontend && npx tsc -b                             # type check
```

The tests run on a throw-away Postgres + pgvector with a fake AI and locally signed RS256 tokens, so they need no
network and no accounts. They cover:
- parsing (PDF pages, DOCX, TXT form feeds, damaged files);
- clause chunking;
- the quote check (exact match, whitespace/quote-mark tolerance, rejected paraphrases and ellipses);
- retrieval scoring and the risk rules;
- Clerk token checks (bad signature, expired, wrong `azp`, no token);
- the AI client for both providers (429 retry, daily-limit message, batching, SSE parsing), with the HTTP layer mocked;
- upload limits and file types;
- the full review pipeline;
- a made-up quote turning into `unclear`;
- rate-limit handling and Retry;
- chat streaming and saved history;
- **cross-user isolation on every route and inside vector search**.

**Evaluation script (bonus).** `eval/samples/` has 2 contracts, and `eval/expected.json` has the right result for each
checklist item. The NDA deliberately has no payment, liability-cap or auto-renewal clause, to test honest "not found" answers.

```bash
.venv/Scripts/python eval/run_eval.py          # uses the AI provider set in .env
```

An item counts as correct only if the status matches **and**, for found items, the verified quote contains the
expected phrase. The script prints a PASS/FAIL table and the score, and saves the details to `eval/results.json`.

**Results:**

| Models | Score |
|---|---|
| Gemini `gemini-3.5-flash-lite` + `gemini-embedding-001` (default) | **15 / 16 (94%)** |
| OpenRouter `nemotron-3-ultra-550b-a55b:free` + `nemotron-3.5-embed-1b:free` | 14 / 16 (88%) |

All 3 "not found" items in the NDA were correctly reported as not found by both. The one Gemini miss is Payment
terms: the model quoted the monthly fee clause, a fair answer, but the eval expects the "thirty (30) days" due-date
clause. I kept the strict expectation rather than loosen it to fit the result.

What the eval caught along the way:
- **5/16 on the first run.** The free model "thinks" before answering. It spent its whole token budget thinking and
  returned an empty answer. Fix: thinking is turned off or kept low (section 7).
- **Quotes that skip a clause number.** Models often join two sub-clauses and drop the number between them. The quote
  check marked these unclear, so I added the narrow rule described in section 8.

## 5. Stack and why

| Layer | Choice | Why |
|---|---|---|
| Backend | **Python 3.12 + FastAPI** | Python has the best document tools (pypdf, python-docx). FastAPI gives typed request/response models, validation, dependency injection for auth, and easy streaming responses. |
| DB | **PostgreSQL 16 + pgvector** | One database for contracts, findings, chat **and** vectors. Scoping vector search to a user is then a plain `WHERE user_id = …`, in the same SQL as everything else. |
| ORM | SQLAlchemy 2 (sync) + psycopg 3 | Sync code is easier to read and explain. The slow work (parsing, AI calls) runs in a worker pool, not the request thread. |
| Auth | **Clerk** (`@clerk/react` v6 + backend JWT check with PyJWT) | Required by the brief. Checking the token by hand with JWKS makes the security path explicit and testable. |
| Frontend | **React 18 + TypeScript + Vite** | Fast dev loop, typed API layer, no heavy framework needed for 2 screens. Plain CSS, responsive down to phones. |
| AI | **Gemini** free tier (default) or **OpenRouter** free models, both through the OpenAI-compatible API | Free. One small client works with both, so the provider and models are `.env` settings. Why Gemini is the default: section 2. |
| Run | Docker Compose (db, api, web/nginx) | One command. nginx serves the built app and proxies `/api` with buffering off so streaming stays smooth. |

## 6. Architecture

```
 Browser (React + Clerk)                     FastAPI backend                                 PostgreSQL + pgvector
 ───────────────────────                     ───────────────                                 ─────────────────────
 Sign in with Clerk ──► session JWT
 every request: Authorization: Bearer JWT ─► auth.current_user()
                                               verify RS256 via Clerk JWKS, exp, azp
                                               user_id = token "sub"   (never from client)
 Upload file ─────────────────────────────►  POST /api/contracts
                                               size ≤20 MB (streamed), type + magic bytes
                                               save Contract(status=processing) + file ───►  contracts, contract_files
                                               hand off to worker pool (2 threads) ──┐
 poll list/detail every 3 s ◄── 201 at once                                          │
                                             worker: review.run_review()  ◄──────────┘
                                               1 parse (pages kept)  2 clause chunking
                                               3 embed chunks in batches  ─────────────────►  chunks(text, offsets, page, vector)
                                               4 per checklist item: hybrid retrieval  ◄────  (WHERE user_id AND contract_id)
                                               5 grounded prompt → JSON {status, summary, quote}
                                               6 find quote in contract text → page/section, else "unclear"
                                               7 risk rules  ───────────────────────────────►  findings
 Checklist + clause viewer ◄─────────────    GET /api/contracts/{id}
 Chat question ──────────────────────────►   POST /api/contracts/{id}/chat  (SSE)
   tokens stream in  ◄── event: token ───     embed question → hybrid retrieval → stream model output
   citations ✓/✗     ◄── event: done  ───     check each quoted passage, save both messages ──►  chat_messages
```

Code map (`backend/app/`):

| File | Job |
|---|---|
| `main.py` | routes, upload limits, ownership check |
| `auth.py` | Clerk token check |
| `models.py` | tables |
| `parsing.py` | PDF/DOCX/TXT to text, with page offsets |
| `chunking.py` | clause-aware chunks |
| `checklist.py` | the 8 items plus their retrieval settings |
| `retrieval.py` | hybrid search |
| `prompts.py` | grounded prompts |
| `quotes.py` | quote check |
| `review.py` | pipeline |
| `chat.py` | streamed chat |
| `risk.py` | "needs attention" rules |
| `llm.py` | AI client (Gemini or OpenRouter) with retries |
| `worker.py` | background pool and restart recovery |

Frontend (`frontend/src/`):

| File | Job |
|---|---|
| `api.ts` | typed client and SSE reader |
| `components/ContractList.tsx` | upload and list |
| `components/ContractView.tsx` | checklist |
| `components/ClauseViewer.tsx` | full text, scroll and highlight |
| `components/Chat.tsx` | chat |

## 7. Chunking and retrieval, per checklist item

**Chunking: one chunk per clause, not every N characters.** Contracts are built from numbered clauses, and a
quote only makes sense inside its clause. `chunking.py` starts a new chunk at:
- numbered headings: `1.`, `4.2`, `12)`;
- `Section 7` / `Clause 7`;
- `ARTICLE V`;
- short ALL-CAPS titles.

A bare `30 days` at the start of a line is *not* a heading, because a dot or bracket is needed. Then:
- A heading-only line (`4. TERMINATION`) is merged into the clause after it.
- Sub-clauses (`4.1`, `4.2`) inherit the parent title ("Termination"), which goes into the embedding text so
  "4.2 …30 days…" is still found as a termination clause.
- Clauses over 1,800 characters are split at paragraph, then line, then sentence edges. Every piece keeps the same
  section number.
- Each chunk stores its **exact start/end character offsets** in the full text and its **page range** (PDF pages,
  and TXT form feeds). DOCX has no fixed pages, so DOCX locations use section numbers.

**Retrieval: hybrid search, scoped to one contract of one user.** For each item we run:
1. **vector search** with a short description of what that clause usually says (pgvector cosine distance);
2. **keyword scoring** with the fixed phrases lawyers use (earlier phrases weigh more).

The two rankings are merged with **reciprocal-rank fusion**, and the model sees the top 6 clauses in document order.
Why hybrid: legal clauses use set phrases ("governed by the laws of", "shall not exceed"), and exact phrases beat
embeddings there. Embeddings catch unusual wording ("Ending the Agreement" instead of "Termination").

| Item | Vector query (meaning) | Strongest keywords | Extra |
|---|---|---|---|
| Parties | "entered into by and between … the parties" | `by and between`, `between`, `parties`, defined names like `("Client")`, `Inc./LLC/Ltd/Pvt` | always adds the **first 2 chunks**: parties are named in the preamble |
| Start date and term | "effective date … term or duration" | `effective date`, `commence`, `term of`, `duration`, `expire` | also adds the first 2 chunks (the start date is often in the preamble, "made on 5 March 2026") |
| Payment terms | "fees, price, invoices, when payment is due" | `payment`, `fee`, `invoice`, `paid`, `due`, `net 30`, currency signs | |
| Termination | "either party may terminate … notice … breach" | `terminat`, `notice`, `breach`, `for convenience`, `cancel` | |
| Limitation of liability | "aggregate liability shall not exceed; no consequential damages" | `limitation of liability`, `liabilit`, `consequential`, `shall not exceed`, `in no event` | |
| Confidentiality | "confidential information must not be disclosed" | `confidential`, `non-disclosure`, `disclos`, `proprietary` | |
| Governing law | "governed by … the laws of …; jurisdiction" | `governed by`, `governing law`, `laws of`, `jurisdiction`, `courts of`, `arbitration` | |
| Auto-renewal | "automatically renews for successive terms unless notice of non-renewal" | `automatically renew`, `auto-renew`, `renew`, `successive`, `non-renewal`, `evergreen` | |

**The prompt keeps findings grounded** (`prompts.py`):
- The clauses are wrapped in `<contract>` tags, each labelled with its location. The model is told they are data, not
  instructions (this protects against prompt injection inside a contract).
- It must answer from those clauses only, and never assume a "standard" clause.
- It must return `not_found` when nothing covers the item.
- The quote must be one passage copied character for character, with no "…", and one short summary goes with it.
- Temperature is 0 and the output is JSON only.
- The model's "thinking" mode is turned off (OpenRouter: `reasoning: {enabled: false}`) or kept low (Gemini:
  `reasoning_effort: low`). Copying a clause needs no long reasoning. With thinking on, the free model spent all its
  tokens thinking and returned empty answers, and chat took about 40 s before the first word appeared instead of
  about 5 s.

Each item gets its own call, so a busy model or a bad answer only affects that one item.

## 8. How quotes are checked

`quotes.find_quote()` runs on the backend for every finding and every chat answer:
1. Strip wrapping quote marks. Reject quotes shorter than 12 characters (they prove nothing) and quotes with `...`/`…`
   (the model skipped words).
2. Normalise both the contract and the quote **only** for differences that text extraction itself creates:
   - runs of spaces and line breaks become one space;
   - curly and straight quotes are treated as the same;
   - the different dash characters are treated as the same;
   - letter case is ignored.

   A character map keeps track of the original positions.
3. Search for the quote in the **whole contract text**. If it appears more than once, prefer a match inside the
   clauses the model was shown.
   - **One narrow exception:** models often join two sub-clauses and drop the clause number between them
     (`…loss of profits. Each Party's…` for `…loss of profits.⏎6.2 Each Party's…`). If the full quote is not found,
     each sentence is checked on its own. The quote is accepted only if every sentence is found word for word, in
     order, and the only text between two sentences is a clause number (`6.2`, `(b)`, `iii.`). The quote shown is
     then the contract's own continuous text, including the `6.2`. Anything else in the gap, even one word, makes
     the item unclear.
4. **Found:** the stored quote is the **contract's own text** for that span, never the model's copy. Page and section
   are worked out from the match position (page offsets and chunk ranges), not taken from the model.
   **Not found:** the item becomes **`unclear`**, no quote is shown, and a note explains why. In chat, unverified
   quotes are shown as "✗ quote not found in contract".
5. In chat, the model is told **not** to write section or page numbers itself. In testing it once labelled a
   Section 7 quote "Section 6", and once added "page 1" to a TXT file that has no pages. The chat shows a
   **✓ location** button for each verified quote instead, computed the same way as above. Clicking it opens the
   clause.

A test (`test_made_up_quote_is_marked_unclear`) feeds the pipeline a made-up quote and checks that every item
becomes `unclear`.

## 9. How each user's data is kept separate

- **Every request is authenticated on the backend.** `auth.current_user` reads `Authorization: Bearer <JWT>` and
  checks:
  - the RS256 signature, using Clerk's JWKS (keys are cached for 1 h);
  - `exp`, `iat` and `nbf`, with 5 s leeway;
  - that `azp` is in `CLERK_AUTHORIZED_PARTIES`.

  It then takes the user id from `sub`. The API never reads a user id from the body, query or headers. Every
  `/api/contracts…` route depends on it.
- **Every table has a `user_id` column, and every query filters on it**: contracts, files, chunks, findings and chat.
  `owned_contract()` loads a contract with `id = :id AND user_id = :me`, and returns **404** (not 403) for someone
  else's id, so the API does not even reveal that the contract exists.
- **Vector search is scoped too.** `retrieval.retrieve()` filters chunks on `user_id AND contract_id` inside the SQL
  that orders by vector distance. A test calls it as user B with user A's contract id and gets nothing back.
- Deleting a contract cascades to its file, chunks, findings and chat (`ON DELETE CASCADE`).
- `test_other_user_cannot_see_or_touch_contract` calls every route (list, get, review, messages, chat, delete) as a
  second user and expects 404 / an empty list.

## 10. API

| Method | Path | What it does |
|---|---|---|
| GET | `/api/health` | liveness (no auth) |
| GET | `/api/contracts` | my contracts: name, size, upload date, status |
| POST | `/api/contracts` | upload (multipart `file`). 413 over 20 MB; 415 for wrong type or fake extension; 422 if empty |
| GET | `/api/contracts/{id}` | contract + findings + clauses |
| DELETE | `/api/contracts/{id}` | delete it, with everything attached |
| POST | `/api/contracts/{id}/review` | re-run the review (Retry after a failure or rate limit) |
| GET | `/api/contracts/{id}/messages` | chat history |
| POST | `/api/contracts/{id}/chat` | ask; the answer streams as Server-Sent Events: `user`, `token`…, `done` (with citations) or `error` |

**Errors** are JSON `{"detail": "plain message"}`. In particular:
- A 429 or 503 from the AI provider is retried with backoff (honouring `Retry-After`). If it still fails, the user sees "The
  free AI model is rate-limited or busy right now. Please wait a minute and try again."
- During a review, the contract is marked **failed** with that message and a **Retry** button. The chunk embeddings
  are kept, so a retry does not embed them again.
- During chat, the error arrives as an SSE `error` event, and the unanswered question is removed from history.

## 11. Assumptions

- **"Page or section number":**
  - PDFs get the page and the section;
  - DOCX gets the section only (Word files have no fixed pages until they are rendered);
  - TXT gets the page only when the file has form-feed page breaks.
- **Scanned PDFs** (images, no text layer) are rejected with a clear message. OCR is out of scope.
- **Review runs once, at upload.** The user can re-run it with Retry after a failure.
- **"Unclear"** is used both when the model says the clause is ambiguous and when its quote cannot be verified.
- **Embedding dimensions:** Gemini embeddings are requested at 1,536 dimensions (`EMBED_DIM`); OpenRouter's
  `nemotron-3.5-embed-1b` returns 2,048. No ANN index is used: search is always limited to one contract (tens to
  hundreds of rows), so an exact scan is fast.

## 12. Known limitations and what I would improve

**Limitations:**
- Only quotes are verified, not summaries. A summary can still add a fact from a nearby clause. Example: the NDA's
  confidentiality summary mentions the 2-year survival period, which is in Section 5, not in the quoted Section 2.
  The fact is true, but it is not backed by the quote shown. A second check that asks the model to justify each
  summary claim from the quote would catch this.
- Free models sometimes send a streamed answer in a few large pieces. The chat reveals received text gradually so
  it reads smoothly; the backend streams tokens as they arrive.
- The background work uses an in-process thread pool. A server restart marks running reviews as failed (the user
  presses Retry). A queue such as Redis + RQ/Celery would survive restarts and scale out.
- Text extraction is plain pypdf, so complex layouts (multi-column pages, tables, headers and footers) can produce
  awkward text. Hyphenated line breaks (`termi-\nnation`) can make a correct quote fail the check, and the item then
  shows as `unclear`. It fails safe.
- Clause detection is rule-based. Contracts with no numbering and no caps titles fall back to paragraph-size pieces.
- Docker Compose is written but I could not run Docker on my build machine. I tested the same stack locally with
  `scripts/run_local.py` (real Postgres + pgvector via pgserver).
- No schema migrations (tables are created at start-up). With more time I would use Alembic.
- **Free tiers have daily request limits** (OpenRouter: 50 per day, which is why Gemini is the default). One review
  uses about 10 requests (1–2 embedding batches + 8 checklist calls), and one chat question uses 2. When a daily limit
  is hit, the app says so plainly instead of retrying. With more time I would cut each review to fewer calls by
  asking about several checklist items per call.

**With more time:**
- Compare two versions of a contract (bonus 1).
- Show the original PDF page with the quote highlighted.
- An OCR fallback.
- A larger evaluation set with per-item precision and recall.
- Per-user upload quotas and API rate limits.
- Frontend tests (Playwright).

## 13. How I used AI tools

- **Claude Code (Claude Opus 5.5)** as a pair programmer:
  - turning the brief into a requirements checklist;
  - drafting the plan and reviewing it;
  - writing most of the first-draft code and tests;
  - running the tests and fixing failures.
- I reviewed every file, chose the design (clause chunking, hybrid retrieval, backend quote check, 404 for other users'
  data) and tested the app by hand.
- The app itself uses Gemini free-tier models at runtime (see sections 2 and 5).
