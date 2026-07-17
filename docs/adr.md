# Architecture Decision Records

Short-form ADRs for the key decisions in this project. Each one names what was chosen, what else was on the table, and what we give up by choosing it — not just why it looks good.

---

## ADR-001: LangGraph over n8n for agent orchestration

**Status:** Accepted

**Context:** The task allows any orchestration tool. The core control flow here isn't a straight pipeline — it needs a bounded retry after a completeness check, and a branch into two different storage paths depending on a validation outcome computed mid-run.

**Decision:** LangGraph.

**Alternatives considered:** n8n, Make, a hand-rolled Python state machine.

**Why:** State and conditional control flow are the actual hard part of this task, not the individual steps. LangGraph models that directly — typed state passed between nodes, conditional edges, a native retry cycle. A visual workflow tool can express the same logic through sub-workflows and code nodes, but at that point it's approximating a state machine rather than using one, and the retry/branch logic ends up living in code anyway, just wrapped in a different UI.

**Consequences:** Everything lives in code, with no visual trace of the flow for a non-technical reader — the architecture diagram in this repo exists specifically to cover that gap. A node-based tool would also integrate more directly with pre-built connectors (email, CRMs, Slack) if this were extended toward those integrations later; LangGraph would need that glue written by hand.

---

## ADR-002: Claude Vision over Tesseract OCR

**Status:** Accepted

**Decision:** Claude Vision handles both scanned PDFs and images.

**Alternatives considered:** Tesseract/pytesseract.

**Why:** Tesseract is free but unreliable on poor scans, unusual fonts, or noisy phone photos — and it's a separate dependency with its own failure modes to handle. Vision costs more per call but is significantly more reliable, and reusing the same model for both vision and text understanding removes a whole tool from the stack.

**Consequences:** Per-page cost is higher than OCR, and there's no local/offline fallback. For a demo with a handful of files this doesn't matter; noted in the README as something to revisit for high-volume production use (hybrid: text extraction where possible, Vision only for scanned pages).

---

## ADR-003: Two independent confidence gates, not one score

**Status:** Accepted

**Decision:** Extraction completeness (Gate 1) and RAG validation rate (Gate 2) are separate checks with separate thresholds and separate responses.

**Alternatives considered:** A single blended confidence score.

**Why:** A missing field and a factually wrong field are different problems. One means "try extracting again." The other means "the data looks complete but doesn't check out — a person needs to look at it." Blending them into one number would make it impossible to tell, from the outside, which kind of problem triggered a review.

**Consequences:** Two thresholds to tune and explain instead of one. Worth it — the alternative saves a few lines of code and costs the reviewer the ability to tell what actually went wrong.

---

## ADR-004: A failing item flags the whole order, not just that line

**Status:** Accepted

**Decision:** If any item fails RAG validation, the entire purchase order goes to human review — not just the failing line item.

**Alternatives considered:** Partial approval — store the valid items, flag only the bad one.

**Why:** Partial approval means partial-order state, partial storage writes, and a notification model that has to explain "8 of 9 items were approved, 1 wasn't" — real complexity for a 72-hour scope. Flagging the whole order is a simpler, more conservative default that's easy to explain and easy to verify in a demo.

**Consequences:** Some orders get fully blocked by one bad line, which is more conservative than a real production system would probably want. Documented as a scope decision, not an oversight.

---

## ADR-005: SQLite over Google Sheets or flat files

**Status:** Accepted

**Decision:** SQLite for both `purchase_orders` and `pending_reviews`.

**Alternatives considered:** Google Sheets, CSV/JSON files.

**Why:** Google Sheets needs OAuth setup — an integration risk with no payoff for a 72-hour window. Flat files work but don't demonstrate relational data handling. SQLite needs zero external setup and still shows real schema and query design.

**Consequences:** Not a production-scale choice — no concurrent write handling, no replication. Fine for a single-process demo; called out as a production gap in the README.

---

## ADR-006: Deterministic CSV parsing before falling back to the LLM

**Status:** Accepted

**Decision:** CSV files with headers matching a known set are converted to JSON directly in code. Only non-matching CSVs go through Claude for extraction.

**Alternatives considered:** Always route CSVs through the LLM, same as PDF/image.

**Why:** A clean, predictable CSV doesn't need an LLM call to parse — doing so anyway would add cost, latency, and a small chance of misreading data that was already structured. Reserving the LLM for genuinely unstructured or non-standard input keeps the common case cheap and fast.

**Consequences:** Adds one more code path to maintain, and the "expected header set" is a hardcoded assumption that needs to be kept in sync with the schema.

---

## ADR-007: Minimal API key auth on `/review/approve`, not JWT

**Status:** Accepted

**Decision:** A single `X-API-Key` header, checked against an environment variable.

**Alternatives considered:** JWT-based auth, no auth at all.

**Why:** No auth on an endpoint that moves orders into the "approved" table would be a real gap, not just a demo simplification. A full JWT/session system is more than this task calls for. A static API key is the minimum that's still real security.

**Consequences:** Not production-grade — a single shared key, no per-user identity, no rotation. Documented in the README as a known scope limit.
