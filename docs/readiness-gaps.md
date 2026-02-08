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
| Process pool dispatch (`cpu_bound=True`) | IMPL | 0 | Requires shared backend config |
| Pipeline-per-listener architecture | MISMATCH | — | Docs say per-listener; code is single unified pipeline |
| Thread scheduling (breadth-first / depth-first) | MISSING | 0 | Config field parsed; `scheduler.py` empty; queue is plain FIFO |
| Fair-share queuing / backpressure | MISSING | 0 | Docs claim "Token-Rate Monitoring and Fair-Share Queuing"; not implemented |

---

## 2. Security & Auth

| Feature | Status | Tests | Notes |
|---------|--------|-------|-------|
| Ed25519 envelope signing (`crypto/signing.py`) | READY | yes | Optional on main bus, enforced on OOB |
| OOB privileged channel (localhost WebSocket) | READY | 80 | Command dispatch, event push |
| Peer tables (thread-scoped privilege tiers) | READY | 16 | Runtime mutation, OOB commands |
| Peer constraint enforcement (dispatch-time) | READY | yes | Re-reads table on every message |
| Handler isolation (coroutine capture boundary) | READY | yes | Handlers cannot forge identity/thread |
| Opaque thread UUIDs | READY | yes | — |
| TLS/WSS on main bus | MISSING | 0 | Config accepts `tls.cert`/`tls.key`; no certificate loading or enforcement |
| TOTP authentication | MISSING | 0 | `AuthConfig.totp_secret_env` field exists; no generation or validation |
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
| fetch | `fetch.py` | IMPL | 0 | **High** — SSRF protection, private IP blocking, URL validation |
| files | `files.py` | IMPL | 0 | **High** — path traversal protection, sandbox allowlist, 10MB limit |
| shell | `shell.py` | IMPL | 0 | **High** — dangerous command blocklist, shell operator filtering |
| search | `search.py` | IMPL | 0 | **Medium** — 3 backends (SerpAPI, Google, Bing), external API calls |
| convert | `convert.py` | IMPL | 0 | **Medium** — XML/JSON parsing, XPath queries, has dead code |
| librarian | `librarian.py` | IMPL | 0 | **Medium** — exist-db REST client, XQuery variable binding via string concat |
| keyvalue | `keyvalue.py` | STUB | 0 | **Low** — in-memory dict; TTL ignored; needs Redis/SQLite backend |

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
| WebSocket event streaming | STUB | 0 | Skeleton endpoint; no push implementation |

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

### P0 — Security-Critical (untested code that handles hostile input)
1. Tests for `fetch.py` (SSRF protection, private IP blocking)
2. Tests for `shell.py` (command blocklist, shell operator filtering)
3. Tests for `files.py` (path traversal, sandbox enforcement)

### P1 — Honesty (fix doc/code mismatches)
4. Fix docs: pipeline is unified, not per-listener
5. Fix docs: TLS/TOTP not enforced (remove "mandatory" or implement)
6. Fix docs: thread scheduling not implemented
7. Fix docs: federation not implemented

### P2 — Completeness (implement stubs)
8. Implement `keyvalue.py` backend (Redis or SQLite)
9. Tests for `search.py`, `convert.py`, `librarian.py`
10. WebSocket event streaming

### P3 — Future (documented aspirations)
11. TLS enforcement on main bus
12. TOTP authentication
13. Federation / gateway runtime
14. Thread scheduling (breadth-first / depth-first)
15. XML meta introspection handlers
16. Fair-share queuing / backpressure
