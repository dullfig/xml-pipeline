# Security Hit List

Adversarial audit conducted 2026-02-09. All items must be resolved before
any deployment with untrusted handlers or untrusted WASM modules.

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

### [ ] 2. Fetch tool: SSRF via redirect following (TOCTOU)
**File:** `xml_pipeline/tools/fetch.py:129`
**Issue:** `httpx.AsyncClient(follow_redirects=True)` — URL is validated once,
then httpx follows `301 → http://169.254.169.254/latest/meta-data/` silently.
**Fix:** Set `follow_redirects=False`. Manually follow redirects with
re-validation of each `Location` header against `_validate_url()`.

### [ ] 3. XXE in five XML parse sites
**Issue:** Default parsers (entities enabled, network enabled) used after the
hardened repair step. Entities survive C14N serialization and are resolved on re-parse.
| File | Line | Context |
|------|------|---------|
| `message_bus/steps/c14n.py` | 44 | `etree.fromstring(c14n_bytes)` after C14N |
| `message_bus/envelope.py` | 78 | `XMLParser(recover=True)` — no entity/network flags |
| `message_bus/pipeline.py` | 41 | `XMLParser(recover=True)` — same |
| `third_party/xmlable/__init__.py` | 22, 28 | `objectify.fromstring()` / `objectify.parse()` |
| `third_party/xmlable/_io.py` | 35 | `objectify_parse(f)` — file deserialization |
**Fix:** Add `resolve_entities=False, no_network=True` to every parser. Define a
shared `_SECURE_PARSER` constant and use it everywhere.

### [ ] 4. Envelope built via f-string (XML injection)
**File:** `stream_pump.py:1209-1216`
**Issue:** `from_id`, `to_id`, `thread_id` interpolated directly into XML string.
If any contain `<`, `>`, `&`, the envelope is malformed or injectable.
**Fix:** Use `xml.sax.saxutils.escape()` on all three fields, or switch to
lxml element construction.

---

## P1 — Significant Risk

### [ ] 5. WASM modules share KV namespace
**File:** `xml_pipeline/wasm/host_functions.py:130, 161`
**Issue:** All modules use `namespace="wasm"`. Module A reads module B's secrets.
**Fix:** Change to `namespace=f"wasm:{module_name}"`. Thread the module name
through `link_host_functions()`.

### [ ] 6. WASM `read_string` / `write_string` no bounds checking
**File:** `xml_pipeline/wasm/host_functions.py:28-47`
**Issue:** `read_string(store, memory, ptr, length)` — no validation that
`ptr >= 0`, `length >= 0`, `ptr + length <= memory.data_size(store)`.
`write_string` doesn't check alloc return value (could be 0 / -1).
**Fix:** Add bounds checks before `memory.read()` / `memory.write()`. Validate
`alloc_fn` returns non-zero pointer within memory bounds.

### [ ] 7. WASM dispatcher trusts `_to` routing field
**File:** `xml_pipeline/wasm/dispatcher.py:188-191`
**Issue:** WASM module returns `{"_to": "arbitrary-listener"}` and it's used
for routing without validation against peer list.
**Fix:** Validate `_to` against the listener's declared peers before routing.

### [ ] 8. OOB inject-message bypasses peer enforcement
**File:** `xml_pipeline/oob/handlers.py:265`
**Issue:** Calls `pump._inject_raw()` directly, skipping routing table context.
Authenticated OOB client can send messages to any listener from any identity.
**Fix:** Route through `pump.inject()` instead, or enforce peer validation in
the OOB handler before injection.

### [ ] 9. OOB TOTP authentication race condition
**File:** `xml_pipeline/oob/server.py:130-185`
**Issue:** Between TOTP check and marking `_authenticated`, concurrent messages
can slip through unauthenticated.
**Fix:** Use `asyncio.Lock()` around the authentication check-and-set, or
buffer all messages until auth completes.

### [ ] 10. Coding swarm prompt injection via `tool_name`
**Files:** `handlers/coding_swarm/architect.py:45`, `coder.py:46`, `tester.py:47`, `reviewer.py:46`
**Issue:** `tool_name` embedded raw in f-string LLM prompts. A name like
`calc'.\nIgnore previous instructions...` injects into the system prompt.
**Fix:** Sanitize `tool_name` — strip to `[a-zA-Z0-9_-]` or use a structured
prompt format that separates user data from instructions.

---

## P2 — Defense in Depth

### [ ] 11. Missing `no_network=True` on hardened parsers
**Files:** `repair.py:5-10`, `utils/message.py:19`
**Issue:** `resolve_entities=False` is set but `no_network=True` is missing.
Belt-and-suspenders against DTD network resolution.
**Fix:** Add `no_network=True` to all `XMLParser()` instances.

### [ ] 12. Librarian XQuery escaping inconsistent
**File:** `xml_pipeline/librarian/query.py:72, 162, 392`
**Issue:** Uses backslash escaping (`\"`) instead of XQuery-correct `""`.
The correct `_escape_xquery_string()` exists at line 69 but isn't used everywhere.
**Fix:** Replace all manual escaping with `_escape_xquery_string()`.

### [ ] 13. Console prompt: phishing / social engineering
**File:** `xml_pipeline/tools/console.py:147-155`
**Issue:** `ConsolePrompt(source="system", text="Enter your password:")` — no
validation on `source`, no rate limiting, no audit log.
**Fix:** Validate `source` is a registered listener name. Add rate limiting.
Log all prompt interactions for forensics.

### [ ] 14. Coding swarm error messages leak internal paths
**Files:** `handlers/coding_swarm/tools.py:143,197,294`, `coordinator.py:105`
**Issue:** Raw exception strings (`FileNotFoundError`, stack traces) returned
in `SwarmMessage.error` field. Leaks KV structure, module paths.
**Fix:** Wrap exceptions in generic messages; log full details server-side only.

### [ ] 15. WASM `free()` skipped on timeout
**File:** `xml_pipeline/wasm/dispatcher.py:165-167`
**Issue:** On `asyncio.TimeoutError`, thread exits before `free_fn` call.
Repeated timeouts exhaust WASM linear memory.
**Fix:** Wrap WASM call in try/finally; call `free_fn` in finally block.
On timeout, the instance is evicted anyway, but cleanup is still good practice.

### [ ] 16. Coding swarm coordinator: no try/finally on state
**File:** `handlers/coding_swarm/coordinator.py:157-568`
**Issue:** If `handle_coordinator()` raises unexpectedly, `_states[tid]` entry
is never cleaned up. Memory leak and stale state on restart.
**Fix:** Wrap in try/finally; delete state entry on unhandled exception.

### [ ] 17. KV store: no TTL validation
**File:** `xml_pipeline/tools/keyvalue.py:112`
**Issue:** Negative TTL accepted (key instantly expired but stored). No max cap.
**Fix:** `if ttl is not None and ttl < 0: raise ValueError`. Add max cap (e.g. 10 years).

### [ ] 18. WASM capability gate not verified at instantiation
**File:** `xml_pipeline/wasm/host_functions.py:172-273`, `loader.py:161-188`
**Issue:** `_validate_exports()` checks exports but not imports. A module
without declared "fetch" capability could still call `host_fetch` if it's
linked on the same linker from a previous module.
**Fix:** Validate module imports match declared capabilities at load time.
Create per-module linkers instead of shared ones.

### [ ] 19. XPath injection in convert tool
**File:** `xml_pipeline/tools/convert.py:213`
**Issue:** `xpath` parameter passed directly to `root.findall(xpath)`.
ElementTree XPath is limited, but predicates could still extract data or crash.
**Fix:** Validate XPath syntax or whitelist allowed patterns.

### [ ] 20. Fetch: hex/octal IP encoding bypass on Linux
**File:** `xml_pipeline/tools/fetch.py:47-63`
**Issue:** `0x7f000001` (hex for 127.0.0.1) is not parsed by Python's
`ipaddress` module but may be resolved by glibc's `gethostbyname()` on Linux.
Safe on Windows (current env), vulnerable on Linux deployments.
**Fix:** Normalize IP representations before validation; reject hostnames
matching `^0[xX0-9]` patterns.
