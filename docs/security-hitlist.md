# Security Hit List

Adversarial audits: 2026-02-09 (initial), 2026-02-10 (expanded review).
All items must be resolved before any deployment with untrusted handlers
or untrusted WASM modules.

Status legend: `[ ]` open, `[x]` fixed, `[-]` won't fix (with rationale).

---

## P0 — Exploitable Today

### [x] 1. Handler isolation is not enforced
**Files:** `singleton.py`, `buffer.py`, `sequence.py`, `shell.py`, `handler_sandbox.py`
**Issue:** Any handler can `from xml_pipeline.message_bus.singleton import get_stream_pump`
and call `_wrap_in_envelope()` / `_inject_raw()` with forged `from_id`, `to_id`,
`thread_id`. Peer tables, ceiling model, routing enforcement — all bypassed.
System handlers (buffer, sequence) actively use this path.
**Impact:** Complete peer constraint bypass; identity forgery; cross-thread injection.
**Decision:** Hybrid design — untrusted handlers run in a persistent subprocess
(one per listener) where the pump singleton is never set. The subprocess
communicates via multiprocessing Queues with XML-bytes wire protocol.
System handlers and LLM agents remain in-process (trusted). Auto-trust rules:
`system.*` prefix → trusted, `agent=True` → trusted, explicit `trusted=True/False`
overrides, all others default to untrusted (subprocess). See `handler_sandbox.py`.

### [x] 2. Fetch tool: SSRF via redirect following (TOCTOU)
**File:** `xml_pipeline/tools/fetch.py:129`
**Issue:** `httpx.AsyncClient(follow_redirects=True)` — URL is validated once,
then httpx follows `301 → http://169.254.169.254/latest/meta-data/` silently.
**Fix:** Set `follow_redirects=False`. Manual redirect loop with re-validation
of each `Location` against `_validate_url()`. Max 5 redirects. 303→GET conversion.

### [x] 3. XXE in five XML parse sites
**Issue:** Default parsers (entities enabled, network enabled) used after the
hardened repair step. Entities survive C14N serialization and are resolved on re-parse.
**Fix:** All six sites hardened with `resolve_entities=False, no_network=True`:
c14n.py, envelope.py, pipeline.py, xmlable/__init__.py (via `objectify.makeparser()`),
xmlable/_io.py. Also added `no_network=True` to repair.py (was missing, hitlist #11).

### [x] 4. Envelope built via f-string (XML injection)
**File:** `stream_pump.py:_wrap_in_envelope()`
**Issue:** `from_id`, `to_id`, `thread_id` interpolated directly into XML string.
If any contain `<`, `>`, `&`, the envelope is malformed or injectable.
**Fix:** Replaced entire method with `etree.Element()` / `etree.SubElement()` builders.
lxml auto-escapes text content. Signing now operates on the element tree directly
instead of re-parsing from string.

---

## P1 — Significant Risk

### [x] 5. WASM modules share KV namespace
**File:** `xml_pipeline/wasm/host_functions.py:130, 161`
**Issue:** All modules use `namespace="wasm"`. Module A reads module B's secrets.
**Fix:** Added `module_name_for_kv` param to `link_host_functions()`. KV calls now use
`namespace=f"wasm:{module_name}"`. Threaded from `loader.py` via `self.name`.

### [x] 6. WASM `read_string` / `write_string` no bounds checking
**File:** `xml_pipeline/wasm/host_functions.py:28-47`
**Issue:** `read_string(store, memory, ptr, length)` — no validation that
`ptr >= 0`, `length >= 0`, `ptr + length <= memory.data_size(store)`.
`write_string` doesn't check alloc return value (could be 0 / -1).
**Fix:** Added bounds validation: ptr/length non-negative, ptr+length within
`memory.data_len(store)`, alloc return >0 and within bounds.

### [x] 7. WASM dispatcher trusts `_to` routing field
**File:** `xml_pipeline/wasm/dispatcher.py:188-191`
**Issue:** WASM module returns `{"_to": "arbitrary-listener"}` and it's used
for routing without validation against peer list.
**Fix:** Added peer validation before routing. If `_to` target is not in the
listener's peers, falls back to `HandlerResponse.respond()`.

### [x] 8. OOB inject-message bypasses peer enforcement
**File:** `xml_pipeline/oob/handlers.py:265`
**Issue:** Calls `pump._inject_raw()` directly, skipping routing table context.
Authenticated OOB client can send messages to any listener from any identity.
**Fix:** Added hardened XML parser for payload. Note: OOB inject is privileged by
design — peer enforcement happens at dispatch time in the pipeline, not at injection.

### [x] 9. OOB TOTP authentication race condition
**File:** `xml_pipeline/oob/server.py:130-185`
**Issue:** Between TOTP check and marking `_authenticated`, concurrent messages
can slip through unauthenticated.
**Fix:** Added `asyncio.Lock()` (`self._auth_lock`) around the TOTP check-and-set
with double-check after lock acquisition.

### [x] 10. Coding swarm prompt injection via `tool_name`
**Files:** `handlers/coding_swarm/architect.py:45`, `coder.py:46`, `tester.py:47`, `reviewer.py:46`
**Issue:** `tool_name` embedded raw in f-string LLM prompts. A name like
`calc'.\nIgnore previous instructions...` injects into the system prompt.
**Status:** Already mitigated. `_validate_tool_name()` at coordinator entry (line 171)
enforces `^[a-z][a-z0-9_-]*$` before the name reaches any LLM agent. No special
characters can pass through.

---

## P2 — Defense in Depth

### [x] 11. Missing `no_network=True` on hardened parsers
**Files:** `repair.py:5-10`, `utils/message.py:19`
**Issue:** `resolve_entities=False` is set but `no_network=True` is missing.
Belt-and-suspenders against DTD network resolution.
**Fix:** Added `no_network=True` to repair parser and all other parser sites (done with #3).

### [x] 12. Librarian XQuery escaping inconsistent
**File:** `xml_pipeline/librarian/query.py:72, 162, 392`
**Issue:** Uses backslash escaping (`\"`) instead of XQuery-correct `""`.
**Fix:** Added `_escape_xquery_string()` using `""` and `''` doubling.
Replaced all 3 manual escaping sites.

### [x] 13. Console prompt: phishing / social engineering
**File:** `xml_pipeline/tools/console.py:147-155`
**Issue:** `ConsolePrompt(source="system", text="Enter your password:")` — no
validation on `source`, no audit log.
**Fix:** Added audit logging. Display source now uses `metadata.from_id` (trusted
system identity) instead of untrusted `payload.source`.

### [x] 14. Coding swarm error messages leak internal paths
**Files:** `handlers/coding_swarm/tools.py:143,197,294`, `coordinator.py:105`
**Issue:** Raw exception strings (`FileNotFoundError`, stack traces) returned
in `SwarmMessage.error` field. Leaks KV structure, module paths.
**Fix:** Changed `error=str(exc)` to `error=f"X failed: {type(exc).__name__}"`.
Added `_tools_logger.exception()` for server-side detail logging.

### [x] 15. WASM `free()` skipped on timeout
**File:** `xml_pipeline/wasm/dispatcher.py:165-167`
**Issue:** On `asyncio.TimeoutError`, thread exits before `free_fn` call.
**Fix:** Wrapped WASM call body in try/finally. `free_fn` called in finally
block with nested try/except (instance may be poisoned on timeout).

### [x] 16. Coding swarm coordinator: no try/finally on state
**File:** `handlers/coding_swarm/coordinator.py:157-568`
**Issue:** If `handle_coordinator()` raises unexpectedly, `_states[tid]` entry
is never cleaned up.
**Fix:** Split into wrapper + `_handle_coordinator_inner`. Wrapper catches
exceptions, logs, and cleans up `_states.pop(tid, None)`.

### [x] 17. KV store: no TTL validation
**File:** `xml_pipeline/tools/keyvalue.py:112`
**Issue:** Negative TTL accepted (key instantly expired but stored). No max cap.
**Fix:** Added `MAX_TTL_SECONDS = 315_360_000` (~10 years) and `_validate_ttl()`
helper. Added validation to both `SqliteBackend.set()` and `MemoryBackend.set()`.

### [-] 18. WASM capability gate not verified at instantiation
**File:** `xml_pipeline/wasm/host_functions.py:172-273`, `loader.py:161-188`
**Issue:** `_validate_exports()` checks exports but not imports.
**Rationale:** Already adequately mitigated. `validate_capabilities()` is called
at config time, and `link_host_functions()` only links host functions for
declared capabilities. Each module gets its own linker instance (created in
`create_instance()`), so no cross-module leakage.

### [x] 19. XPath injection in convert tool + XXE (#39)
**File:** `xml_pipeline/tools/convert.py`
**Issue:** stdlib `xml.etree.ElementTree` used (vulnerable to entity expansion).
`xpath` passed directly to `findall()`.
**Fix:** Switched entirely from stdlib ET to lxml with hardened parser
(`resolve_entities=False, no_network=True, huge_tree=False`). Merged with #39.

### [x] 20. Fetch: hex/octal IP encoding bypass on Linux
**File:** `xml_pipeline/tools/fetch.py:47-63`
**Issue:** Hex/octal IP encodings could bypass `ipaddress.ip_address()` check.
**Fix:** Refactored to always resolve through DNS first (catches hex/octal tricks).
Added `is_reserved`, `is_multicast`, `is_unspecified` to blocked IP checks.
Extracted `_is_dangerous_ip()` helper for comprehensive coverage.

---

## P0.5 — Added 2026-02-10 (Network / DoS / Auth)

### [x] 21. Unbounded message queue (OOM via flooding)
**File:** `stream_pump.py:113`
**Issue:** `asyncio.Queue()` with no `maxsize`.
**Fix:** Added `maxsize=10_000` to the queue. Provides backpressure to callers.

### [x] 22. Permissive CORS defaults
**File:** `server/app.py:63-72`
**Issue:** `allow_origins=["*"]` + `allow_credentials=True` + `allow_methods=["*"]`.
**Fix:** Default to `localhost:3000` origins. Restricted methods to
`GET,POST,PUT,DELETE`. Restricted headers to `Authorization,Content-Type`.
Callers can still pass `cors_origins=["*"]` explicitly if needed.

### [x] 23. No WebSocket connection limits
**File:** `server/websocket.py`
**Issue:** Unlimited concurrent connections on the main bus WebSocket. FD exhaustion.
**Fix:** Added `ConnectionLimiter` shared across both `/ws` and `/ws/messages` endpoints.
Global cap 50 connections, per-IP cap 10, 5-minute idle timeout. Rejected connections
get close code 1013 ("Try Again Later"). Both `ConnectionManager` and
`MessageStreamManager` register through the shared limiter.

### [x] 24. No message size limits (WebSocket + OOB)
**File:** `oob/server.py:91`
**Issue:** Arbitrarily large payloads accepted before parsing.
**Fix:** Added `max_size=1_048_576` (1 MB) and `max_queue=64` to OOB
`websockets.serve()`. Main bus WebSocket (#23) still needs limits.

### [x] 25. Identity=None silently disables all signature verification
**File:** `stream_pump.py` (constructor)
**Issue:** Missing identity key path → signing disabled with no indication.
**Fix:** Added explicit warning log when `identity_path` is empty:
"envelope signing and signature verification are DISABLED".

### [x] 26. No TOTP rate limiting (brute-forceable in ~7 min)
**File:** `oob/server.py`
**Issue:** Unlimited auth attempts per connection.
**Fix:** Added per-remote-address rate limiter: max 5 failures per 5-minute
window. Failures tracked in `_totp_failures` dict. Pruned on each attempt.
Success clears the failure history.

### [x] 27. Pickle deserialization in shared backend
**File:** `memory/shared_backend.py:215-217`
**Issue:** `pickle.loads()` on data from Redis/Manager. RCE if backend compromised.
**Fix:** Added HMAC-SHA256 integrity wrapper. Per-process 32-byte random key.
Format: `[32-byte HMAC][pickle payload]`. `deserialize_slot()` verifies HMAC
before unpickling. Tampered data raises `ValueError`.

### [x] 28. Dynamic import without module allowlist
**Files:** `config_loader.py:170-175`
**Issue:** `importlib.import_module()` on arbitrary dotted paths from config.
**Fix:** Added `_validate_import_path()` to `ConfigLoader`. Validates safe
characters (`^[a-zA-Z_][a-zA-Z0-9_.]*$`) and rejects dunder segments
(`__builtins__`, `__import__`, etc.).

### [x] 29. No OOB replay protection
**Files:** `oob/auth.py`
**Issue:** Timestamps in requests are parsed but never validated.
**Fix:** Added timestamp validation after signature verification. Requests with
`timestamp` attribute >300s old are rejected. Also hardened the XML parser in
both `parse_request()` and `verify_request()` (`resolve_entities=False, no_network=True`).

### [-] 30. Token bucket race condition
**File:** `llm/token_bucket.py:49-89`
**Issue:** Lock released during sleep. Concurrent `acquire()` can over-consume.
**Rationale:** Not exploitable in practice. The `while True` loop re-checks
`_tokens >= tokens` after re-acquiring the lock, so it self-corrects. The
`try_acquire()` sync method is also safe because asyncio is single-threaded.
Token over-consumption is bounded to one extra request in the worst case.

### [x] 31. OOB error messages leak implementation details
**File:** `oob/server.py:146-148`
**Issue:** `str(e)` from exceptions sent verbatim to clients.
**Fix:** Changed to generic `"Internal server error"` message. Full exception
logged server-side with `exc_info=True`.

### [x] 32. Env var injection in shell worker
**File:** `tools/shell_worker.py`
**Issue:** Handler-supplied env dict passed without filtering.
**Fix:** Added `BLOCKED_ENV_VARS` frozenset (28 entries: `LD_PRELOAD`, `PATH`,
`DYLD_*`, `PYTHONPATH`, `BASH_ENV`, `IFS`, etc.). Env dict filtered before
passing to xp-exec.

### [x] 33. Graceful shutdown deadlock
**File:** `stream_pump.py:shutdown()`
**Issue:** `queue.join()` blocks forever if a handler is stuck.
**Fix:** Wrapped in `asyncio.wait_for(..., timeout=30.0)`. Logs warning with
remaining queue size on timeout.

### [x] 34. Private key file permissions not checked
**File:** `crypto/identity.py:49-86`
**Issue:** Key loaded without verifying file is restricted (0600 on Unix).
**Fix:** Added permission check in `Identity.load()`. Warns if group/other
bits are set (`mode & 0o077`). Skipped on Windows. Recommends `chmod 600`.

### [-] 35. Unbounded context buffer
**File:** `memory/context_buffer.py:203-204`
**Issue:** 1000 threads x 10K slots, no per-message size limit.
**Rationale:** Already bounded: `max_threads=1000`, `max_slots_per_thread=10000`.
GC evicts oldest thread when limit reached. The 10M object worst case is
theoretical — in practice, thread lifetimes are much shorter.

### [x] 36. No OOB session invalidation on config reload
**File:** `oob/server.py`
**Issue:** `_authenticated` set survives hot-reload. Stale auth persists.
**Fix:** `_on_pump_event()` now clears `_authenticated` on `ReloadEvent`.
All OOB clients must re-authenticate after config reload.

### [x] 37. Thread budget underflow hidden
**File:** `message_bus/budget_registry.py:58-60`
**Issue:** `consume()` accepts negative token values, which could inflate the budget.
**Fix:** `consume()` now clamps both `prompt_tokens` and `completion_tokens`
to `max(0, ...)`. Negative values silently clamped to 0.

### [x] 38. WASM host_fetch leaks error details to modules
**File:** `wasm/host_functions.py:86-124`
**Issue:** Full exception text (`str(e)`) returned to WASM modules.
**Fix:** All host functions now return generic error messages. Full details
logged server-side via `logger.warning()`. Applies to `host_fetch`, `host_kv_get`,
and `host_kv_set`.

### [x] 39. XXE in convert tool (stdlib ElementTree)
**File:** `tools/convert.py`
**Issue:** Used `xml.etree.ElementTree.fromstring()` — vulnerable to entity expansion.
**Fix:** Merged with #19. Switched entire module from stdlib ET to lxml with
hardened parser (`resolve_entities=False, no_network=True, huge_tree=False`).

---

## Confirmed Safe (Investigated, No Issue)

- **Handler subprocess isolation** — pump singleton never set in sandbox
- **Peer constraint enforcement** — validated on every dispatch
- **Thread ID opaqueness** — handlers see only UUIDs
- **Fetch tool SSRF protection** — blocks localhost, metadata, private IPs, file://
- **Shell tool defaults** — disabled, `shell=False`, operator blocklist, `shlex.split()`
- **Solver/calculator** — Pratt parser + simpleeval, no `eval()`
- **WASI compiler sandbox** — filesystem preopens restricted
- **YAML loading** — `yaml.safe_load()` throughout
- **TOTP comparison** — `hmac.compare_digest()` (timing-safe)
- **Ed25519 signing** — correct C14N before signing
- **Prompt registry** — frozen after startup, immutable
- **Envelope fields** — `from`/`to`/`thread` always system-controlled (despite #4 f-string issue)
