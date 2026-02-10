# AgentServer — Production Readiness Gaps
**February 7, 2026**

Systematic audit of what's implemented, tested, stubbed, or missing vs what the docs claim.

---

## Status Key

| Icon | Meaning |
|------|---------|
| READY | Implemented + tested |
| IMPL | Implemented, no tests |
| STUB | Partial / in-memory placeholder |
| MISSING | Documented but not implemented |
| MISMATCH | Docs describe something different from code |

---

## 1. Core Pump & Pipeline

| Feature | Status | Tests | Notes |
|---------|--------|-------|-------|
| Message pump (repair, C14N, validate, route, dispatch) | READY | 100+ | Solid, production-grade |
| Handler contract (HandlerResponse, metadata, `.respond()`) | READY | integrated | — |
| Context buffer (per-thread, immutable slots) | READY | yes | — |
| Broadcast routing (multiple listeners per root tag) | READY | yes | — |
| Hot-reload (diff-based, OOB-triggered) | READY | 20+ | — |
| Handler timeout (`asyncio.wait_for`, per-listener) | READY | 6 | Default 30s, configurable |
| Thread lifecycle cleanup (`_cleanup_thread`) | READY | 4 | Budgets, todos, workers, events |
| Concurrency controls (per-agent semaphore + global task_limit) | READY | yes | aiostream `task_limit` |
| Process pool dispatch (`cpu_bound=True`) | REJECTED | — | Removed — use `asyncio.to_thread()` (WASM), worker registry (background), or async handlers |
| Pipeline-per-listener architecture | REJECTED | — | Unified pipeline is intentional — re-injection ensures tamper-proof envelopes, consistent events, and per-hop peer enforcement on every message |
| Thread scheduling (breadth-first / depth-first) | REJECTED | 0 | FIFO + async concurrency is sufficient; depth-first optimization deferred to if queue contention is observed in production |
| Fair-share queuing / backpressure | MISSING | 0 | Docs claim "Token-Rate Monitoring and Fair-Share Queuing"; not implemented |

---

## 2. Security & Auth

| Feature | Status | Tests | Notes |
|---------|--------|-------|-------|
| Ed25519 envelope signing (`crypto/signing.py`) | READY | yes | Optional on main bus, enforced on OOB |
| OOB privileged channel (localhost WebSocket) | READY | 80 | Command dispatch, event push |
| Peer tables (thread-scoped privilege tiers) | READY | 52 | Runtime mutation, OOB commands, YAML declarations, ceiling enforcement, parent hierarchy |
| Peer constraint enforcement (dispatch-time) | READY | yes | Re-reads table on every message |
| Handler isolation (coroutine capture boundary) | READY | yes | Handlers cannot forge identity/thread |
| Opaque thread UUIDs | READY | yes | — |
| TLS/WSS on main bus | MISSING | 0 | Config accepts `tls.cert`/`tls.key`; no certificate loading or enforcement |
| TOTP authentication (OOB channel) | READY | 35 | RFC 6238 stdlib TOTP; OOB connection handshake; CLI `keygen --totp` |
| Federation / gateway forwarding | MISSING | 0 | Config schema parsed; zero runtime — no outbound WSS, no identity verification |

---

## 3. LLM Router & Billing

| Feature | Status | Tests | Notes |
|---------|--------|-------|-------|
| Multi-backend router (xAI, Anthropic, OpenAI, Ollama) | READY | 44+ | All 4 providers implemented |
| Strategy selection (failover, round-robin, least-loaded) | READY | yes | — |
| Retry with exponential backoff + jitter | READY | yes | Respects `Retry-After` header |
| Rate limiting (token bucket + concurrency semaphore) | READY | yes | Two-layer system |
| Per-thread token budget enforcement | READY | 15 | `BudgetExhaustedError`, threshold warnings |
| Per-agent usage tracking | READY | 11 | — |
| Platform billing (`UsageTracker` + SQLite store) | READY | 13 | Cost estimation, daily aggregation |
| `platform.complete()` (LLM call assembly) | READY | yes | Prompt registry + buffer + router |

---

## 4. Native Tools

| Tool | File | Status | Tests | Security Surface |
|------|------|--------|-------|------------------|
| calculate | `calculate.py` | READY | 35 | Low — pure math, simpleeval sandbox |
| fetch | `fetch.py` | READY | 67 | httpx-based; SSRF protection, private IP blocking, URL validation; handler + payload classes |
| files | `files.py` | READY | 17 | Disabled (`TOOL_ENABLED=False`); path traversal blocking, sandbox enforcement tested |
| shell | `shell.py` | READY | 56 | OS-isolated via xp-exec worker; disabled @tool (defense-in-depth); handler + payload classes (ShellCommand/ShellResult); 39 validation + 17 handler/payload tests |
| search | `search.py` | READY | 18 | Medium — 3 backends (SerpAPI, Google, Bing), all mocked |
| convert | `convert.py` | READY | 43 | Medium — input size limit (1MB), tag name validation added |
| librarian | `librarian.py` | READY | 31 | Medium — XQuery/Lucene injection fixed, path traversal blocked |
| keyvalue | `keyvalue.py` | READY | 39 | Low — SQLite backend via aiosqlite, TTL, in-memory fallback |

---

## 5. Introspection & Meta

| Feature | Status | Tests | Notes |
|---------|--------|-------|-------|
| REST API (`/api/v1/capabilities`, `/agents`, `/threads`, `/usage`) | READY | yes | FastAPI, comprehensive |
| XML meta introspection (`request-schema`, `request-example`, `list-capabilities`) | MISSING | 0 | Only REST exists; no XML message handlers in pump |
| Meta config flags (`allow_schema_requests`, etc.) | MISSING | 0 | Config parsed; never enforced |

---

## 6. Infrastructure & CLI

| Feature | Status | Tests | Notes |
|---------|--------|-------|-------|
| CLI: `run`, `serve`, `init`, `check`, `version`, `keygen` | READY | — | All functional |
| Background workers (`WorkerRegistry`) | READY | 29 | multiprocessing, thread-scoped |
| Config loading (monolithic + split) | READY | yes | — |
| WebSocket event streaming | READY | 22 | ConnectionManager + MessageStreamManager tested; inject pump wiring is TODO |

---

## 7. Documentation vs Code Mismatches

| Document | Claim | Reality |
|----------|-------|---------|
| message-pump-v2.1.md | "Pipeline-per-listener — each listener owns one dedicated preprocessing pipeline" | Single unified pipeline; all listeners share the same step chain |
| core-principles-v2.1.md | "Mandatory WSS (TLS) + TOTP on main port" | Neither enforced; config fields exist but no runtime code |
| core-principles-v2.1.md | "Token-Rate Monitoring and Fair-Share Queuing" | Basic FIFO + semaphores only; no fair scheduling |
| configuration.md | `thread_scheduling: "breadth-first"` is configurable | Field parsed into OrganismConfig; never used by dispatcher |
| configuration.md | Gateways with `remote_url` and `trusted_identity` | Config parsed; zero federation runtime |
| core-principles-v2.1.md | "Agents can query schemas via meta namespace" | Only REST introspection; no XML meta message handlers |
| core-principles-v2.1.md | "These principles are now locked for v2.1" | Several "locked" features are config-only stubs |

---

## Priority Order (Suggested)

### P0 — Security-Critical (untested code that handles hostile input) — DONE
1. ~~Tests for `fetch.py`~~ — 67 tests: URL validation, private IP, SSRF, httpx tool, handler, roundtrip
2. ~~Tests for `shell.py`~~ — 56 tests: blocklist, operator filtering, payloads, handler, OS user resolution, xp-exec worker
3. ~~Tests for `files.py`~~ — 17 tests: traversal blocking, sandbox enforcement, disabled gate

### P1 — Honesty (fix doc/code mismatches) — DONE
4. ~~Fix docs: pipeline is unified, not per-listener~~ — annotated in core-principles, message-pump
5. ~~Fix docs: TLS/TOTP not enforced~~ — changed "mandatory" to "not yet enforced" in core-principles
6. ~~Fix docs: thread scheduling not implemented~~ — annotated in configuration.md
7. ~~Fix docs: federation not implemented~~ — annotated in core-principles, configuration.md
- Also fixed: introspection (meta) rewritten as operator-only; todo_nudge marked not yet implemented; fair-share queuing annotated; "locked for v2.1" softened

### P2 — Completeness (implement stubs) — DONE
8. ~~Implement `keyvalue.py` backend~~ — SQLite via aiosqlite, TTL support, in-memory fallback (39 tests)
9. ~~Tests for `search.py`, `convert.py`, `librarian.py`~~ — 18 + 43 + 31 tests; security fixes: input size limit + tag validation (convert), XQuery/Lucene injection + path traversal (librarian)
10. ~~WebSocket event streaming~~ — ConnectionManager/MessageStreamManager tested (22 tests); inject pump wiring marked TODO (requires payload class resolution)

### P3 — Future (documented aspirations)
11. TLS enforcement on main bus
12. ~~TOTP authentication~~ — DONE: RFC 6238 stdlib TOTP for OOB channel (35 tests)
13. Federation / gateway runtime
14. Thread scheduling (breadth-first / depth-first)
15. XML meta introspection handlers
16. Fair-share queuing / backpressure
17. ~~YAML peer table declarations~~ — DONE: subtract-only hierarchy with ceiling enforcement (36 tests)
