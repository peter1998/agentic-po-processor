# Agentic PO Processor

Agentic AI pipeline for purchase order processing — extraction, RAG validation, and human-in-the-loop review.

Built as a technical test task for the **Agentic AI Engineer** position at Flat Rock Technology.

---

## Overview

A company receiving purchase orders by email — as a PDF, a photo of a signed document, or a CSV export — normally has someone open each one, type the details into a system, sanity-check the supplier and price, and decide whether to approve it.

This agent handles that first pass. It picks up the email, reads the attachment regardless of format, pulls out supplier/items/quantities/prices, and checks that against what the company already has on file — approved suppliers, expected price ranges. Orders that check out get saved. Orders with a missing field, an unrecognized supplier, or a price outside the normal range get set aside for a person to look at, with the reason attached.

**For a product manager:** this is a triage layer, not a replacement for judgment. It's meant to remove the routine orders from a reviewer's queue so their time goes to the ones that actually need a decision.

**For a reviewer:** you'll only see orders the system couldn't confidently process, and you'll see why — a missing field and a validation mismatch are flagged separately, so you know what you're checking before you open it.

**For an engineer picking this up later:** each design decision below comes with the trade-off it made, not just what was built but why it was built this way instead of the obvious alternative.

---

## Why this scenario (Purchase Orders)

The task explicitly ruled out invoices — "every automation platform has a ready-made template for that." Purchase orders were chosen instead because they hit every requirement in a way that's genuinely demonstrable, not decorative:

- They have a natural structure (supplier, items, quantities, prices), so extraction has a clear target.
- They have a natural source of truth to validate against (a list of approved suppliers, expected price ranges) — so RAG has real work to do, not just a lookup to fake.
- They have natural ambiguity (a missing field, a price out of range, an unrecognized supplier) — so the human-review path isn't hypothetical, it's something the system will actually hit.

---

## Code organization

```
agents/graph.py         LangGraph wiring — nodes, conditional routing, the retry loop
services/llm.py          Claude text/vision extraction, RAG reasoning calls
services/vectorstore.py  ChromaDB + Voyage indexing and retrieval
services/storage.py      SQLite — normalized orders, JSON-snapshot review queue
utils/gates.py           Gate 1 / Gate 2 math — pure functions, no LLM calls, no I/O
utils/csv_parser.py      Deterministic CSV path (ADR-006)
utils/config.py          Settings, loaded once from .env
models/schema.py         Pydantic models — domain data and LangGraph state
tests/                   Unit tests for utils/gates.py and utils/csv_parser.py
```

The gate math and CSV parsing logic live in `utils/`, separate from the LangGraph node functions in `agents/graph.py`, specifically so they can be unit tested in isolation — no mocking an LLM or a graph run needed to verify a threshold calculation.

`GraphState` (in `models/schema.py`) is the object LangGraph passes between nodes — it carries the extracted data, retry count, and gate results as the pipeline runs. It's a separate model from `PurchaseOrder` on purpose: `PurchaseOrder` is business data that gets stored, `GraphState` is pipeline-only bookkeeping that never needs to persist past a single run.

---

## System design — problem by problem

### Problem 1: The sender shouldn't have to wait for processing to finish

An email attachment might need OCR/vision processing and multiple LLM calls — that can take 10–30 seconds. If the webhook holds the connection open that whole time, it looks broken from the sender's side, and it doesn't scale if orders arrive in bursts.

**Solution:** The webhook (`POST /webhook/email`) responds immediately with `202 Accepted`, then hands the actual work to a background task. The sender gets instant confirmation; the real processing happens independently.

The trade-off: FastAPI's `BackgroundTasks` loses in-flight work if the server restarts mid-run. Fine for a 72-hour demo. In production this would move to a durable queue — Redis + RQ or Celery.

### Problem 2: A file could be a clean PDF, a scanned image, or a spreadsheet — and each needs different handling

**Solution:**
- **Text-based PDF** → `pdfplumber` extracts text directly. Fast, cheap, no LLM call needed.
- **Scanned PDF or image** → Claude Vision reads it directly instead of running OCR (Tesseract) first. If `pdfplumber` extracts fewer than 20 characters from a PDF, it's treated as a scan with no text layer and routed to the same Vision path as images — a text-based PDF and a scanned one need different handling, and guessing wrong in either direction either wastes a Vision call or feeds near-empty text to the extraction model.
- **CSV** → loaded with `pandas`. If the column headers match an expected set exactly (`supplier_name`, `product_code`, `quantity`, `unit_price`), it converts to JSON directly in code, no LLM involved. Anything else falls back to the same Claude extraction path as PDF/image. Clean CSVs stay cheap and predictable; only the messy ones cost an LLM call.

### Problem 3: An LLM extraction can be incomplete, and validating incomplete data is worse than useless

Run RAG validation against a half-filled record and you can get a confident-sounding answer about data that isn't even real.

**Gate 1 — Extraction Completeness:** after extraction, the agent checks what fraction of required fields (`supplier.name`, `items[].product_code`, `items[].quantity`, `items[].unit_price`) actually got filled in.

- Below `0.6` → retry extraction once with an adjusted prompt.
- Still below `0.6` after that → stop, route to human review.

The retry cap exists because an unreadable file could otherwise send the agent into an open-ended loop, burning time and API cost for no gain. One retry is enough to catch a bad first pass without leaving the failure mode unbounded.

*A quantization detail worth naming:* for a single-item order, the four required fields mean the fraction can only land on 0, 0.25, 0.5, 0.75, or 1.0. At a 0.6 threshold, a single missing field (0.75) always passes Gate 1, while two missing fields (0.5) always fails. The demo file for this scenario (`missing_field_order.pdf`) deliberately omits two fields, not one, to actually exercise the retry-then-review path — this was caught by running the full pipeline end to end, not by inspecting the threshold math in isolation.

### Problem 4: Extracted data can be *complete* but still *wrong*

A supplier name or product code can be sitting right there in the extracted JSON and still be wrong — nonexistent, or outside agreed terms.

**RAG validation:** a small knowledge base (approved suppliers, item catalog with price ranges, a few sample policies) sits in ChromaDB, indexed with Voyage AI embeddings (`voyage-4-lite`) — Voyage over the more common OpenAI default because it's purpose-built for retrieval and it's what Anthropic recommends pairing with Claude.

Retrieval by itself doesn't count as validation here. The closest-matching supplier name in the database might just be *textually* close — a near-match on the name could easily be a different company. So there's a second step: the extracted data and the retrieved document both go to the LLM with one question — does this actually match, or is there a discrepancy? That reasoning call, not the similarity score, is what decides valid or invalid per item.

**Gate 2 — RAG Validation Rate:** once every item has a verdict, the system works out what percentage passed.

- `≥ 75%` → order accepted, stored.
- `< 75%` → the entire order goes to human review, not just the failing line.

Splitting an order — auto-approving the good lines, flagging only the bad one — is possible, but it means partial-order state, partial storage, partial notifications, more than a 72-hour scope should take on. Flagging the whole order is the simpler call, and it's simpler on purpose, not because splitting wasn't considered.

The RAG corpus is indexed once, at server startup, and skipped on subsequent restarts if it's already populated — re-embedding a small, unchanged corpus on every restart would just be paying the Voyage API for no reason. A `force=True` flag exists to rebuild it deliberately when the corpus data changes.

### Problem 5: Someone needs to be able to act on a flagged order without a custom UI

Flagged orders go into a `pending_reviews` table with the full context of why they were flagged. `GET /review/pending` lists everything currently waiting — without it, a reviewer would need to already know an order's ID to do anything, which isn't how a real review queue works. `POST /review/approve`, protected with an `X-API-Key` header, lets a reviewer approve an order by ID and moves it into `purchase_orders`. There's no UI — a callable, authenticated pair of endpoints is enough to prove the review loop actually closes, which is the part that matters for this task.

### Problem 6: When something goes wrong, someone needs to be able to find out why

Every LLM call, RAG lookup, and routing decision is traced through LangSmith, plus structured JSON logs carrying a `correlation_id` that ties one order's journey together end to end. It's a small addition, and it's the reason a bad outcome is traceable instead of a mystery.

Getting this actually working took two fixes past "set the env vars": the raw `anthropic` SDK bypasses LangChain's auto-instrumentation entirely, so calls need to go through `langsmith.wrappers.wrap_anthropic()` to show up at all. And `pydantic-settings`' `env_file=` only populates our own `Settings` object — it doesn't inject values into the process's real `os.environ`, which is what the LangSmith SDK actually reads. Without an explicit `load_dotenv()` call, tracing silently did nothing despite every env var being "set" as far as our own code was concerned.

---

## Two confidence gates, kept separate on purpose

One overall "confidence score" would be simpler to log and simpler to explain in a sentence. It would also hide two different failure modes behind one number:

| Gate | Question it answers | When it runs | Response if it fails |
|---|---|---|---|
| **Gate 1** — Extraction Completeness | Did we get all the required fields? | Right after extraction, before RAG | Retry once, then route to review |
| **Gate 2** — RAG Validation Rate | Is what we got actually correct? | After RAG validation | Route to review, no retry |

A missing field and a bad price are different problems that need different fixes — collapsing them into one score would mean a reviewer opens a flagged order with no idea which kind of problem they're looking at.

---

## Found and fixed during testing

### During pipeline testing with mocked LLM/RAG calls

Two gaps surfaced while running the full pipeline end to end with mocked calls — not caught by unit tests alone, since both only show up when the pieces run together:

- **RAG corpus wasn't indexed automatically.** The vector store code worked in isolation, but nothing called it at startup — the first real request would have hit an empty ChromaDB collection. Fixed by indexing at startup, with a check to skip re-indexing if the corpus is already populated.
- **Scanned PDFs had no Vision fallback.** `parse_file` ran every PDF through `pdfplumber` regardless of whether it actually had a text layer, so a real scan would have produced empty text instead of triggering Vision. Fixed with a length check on the extracted text.

### During integration testing with real API keys

Real calls to Claude Sonnet 5 surfaced three things mocked testing couldn't have caught, since mocks only ever return the shape of data we told them to:

- `response.content[0]` isn't always the text block. Extended thinking means Claude can prepend a `ThinkingBlock` before the actual `TextBlock`. Code that assumed position 0 crashed with `AttributeError: 'ThinkingBlock' object has no attribute 'text'` on the first real extraction call. Fixed by searching `response.content` for the block with `type == "text"` instead of assuming an index.
- Claude wrapped JSON in markdown fences despite the prompt explicitly saying not to. Real responses came back as ` ```json\n{...}\n``` ` anyway. `_parse_llm_json` now strips fences before calling `json.loads()`, instead of trusting the instruction alone.
- A corrupted PDF crashed the background task instead of routing to review. `parse_file` had no error handling around `pdfplumber.open()` — tested with random bytes given a `.pdf` extension, which raised an unhandled exception. Fixed with a try/except that routes straight to human review, skipping the retry loop since a broken file won't become readable on a second attempt.

---

## Left out, on purpose

- **Real email integration (IMAP/SendGrid):** the task allows "a webhook or trigger" — a live inbox integration adds complexity without adding to what's actually being evaluated here.
- **Idempotency:** the same email arriving twice (a provider retry, say) would currently get processed twice. Production fix: dedupe by file hash combined with sender and subject.
- **Multi-page PDF cost:** every scanned page currently goes through Vision. Production would extract text where possible and fall back to Vision only for scanned pages, to cut cost and latency.
- **Durable task queue:** `BackgroundTasks` doesn't survive a server restart. Production would move to Redis-backed queueing.
- **Auth on `/review/approve`:** the `X-API-Key` check is real but minimal — enough to show the endpoint isn't wide open, not a full auth system.
- **Auth on `/webhook/email`:** currently open — anyone who can reach the server can submit a file. Production would verify the sender (signed webhook from the email provider, or a shared secret) before accepting anything.

These are scope calls, not things that got missed.

---

## User stories

| ID | As a... | I want... | So that... |
|---|---|---|---|
| US-1 | Purchasing employee | to submit a PO by sending an email | I don't need to learn a new tool |
| US-2 | Agentic system | to extract structured data from any supported file format | downstream steps can work with consistent data |
| US-3 | Agentic system | to validate extracted data against internal knowledge | invalid or suspicious orders are caught early |
| US-4 | Purchasing manager | clean orders to be stored automatically | my team isn't bottlenecked reviewing routine orders |
| US-5 | Approvals reviewer | to see only the orders the system is uncertain about, with context | I can focus on genuine edge cases |

---

## Tech stack

| Component | Choice | Reasoning |
|---|---|---|
| LLM | Claude Sonnet 5 | Also handles vision, reducing the stack's moving parts |
| Embeddings | Voyage AI (`voyage-4-lite`) | Purpose-built for retrieval; Anthropic-recommended |
| Agent framework | LangGraph | Native support for stateful, conditional, cyclic flows (needed for the retry loop) |
| Vector DB | ChromaDB | Lightweight, no external server needed for this scope |
| Storage | SQLite | No external dependency, still demonstrates relational data handling |
| API | FastAPI | Async-native, easy background task support |

---

## Architecture

![Architecture diagram](docs/architecture_diagram.png)

*From user need (left) to technical flow (right) — email intake through validation gates to storage and human review.*

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env  # add your API keys
uvicorn main:app --reload
```

`pdf2image` requires `poppler-utils` at the system level (`sudo apt-get install poppler-utils` on Debian/Ubuntu) — `pip install` alone won't cover it.

## Testing

```bash
pytest tests/
```

15 unit tests cover the deterministic logic — Gate 1/Gate 2 threshold math (`utils/gates.py`) and CSV header matching (`utils/csv_parser.py`) — with no LLM calls involved, so they run in well under a second.

8 more (`tests/test_llm_parsing.py`) lock in the three real-API bugs found during integration testing — the `ThinkingBlock` handling and markdown-fence stripping specifically — using plain stand-in objects instead of real API calls, so they stay fast and don't need a working key. A `conftest.py` sets placeholder env values before collection, so the full suite runs on a machine with no `.env` file at all.

```bash
pytest tests/ --cov=utils --cov=models --cov-report=term-missing
```

`utils/gates.py` and `models/schema.py` are at 100% coverage; `utils/csv_parser.py` at 94% (one untested branch: an edge case with correct headers but zero data rows). `utils/config.py` shows 0% under this command because it requires real environment variables to import — it's exercised by the running application, not by the unit test suite.

The end-to-end path is demonstrated with four sample files (`data/demo_files/`): a valid order, one with a missing field, one with a conflicting price, and one that's simply not a readable PDF — see the screen recording for a live walkthrough. Total API cost for running all four through the full pipeline was under $1 — the CSV file skips the LLM entirely (deterministic path), and the rest are a handful of small extraction and reasoning calls, not bulk processing.