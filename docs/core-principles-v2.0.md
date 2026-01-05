# AgentServer v2.0 — Core Architectural Principles
**January 03, 2026**  
**Architecture: Autonomous Schema-Driven, Turing-Complete Multi-Agent Organism**

These principles are the single canonical source of truth for the project. All documentation, code, and future decisions must align with this file.

## Identity & Communication
- All traffic uses the universal `<message>` envelope defined in `envelope.xsd` (namespace `https://xml-pipeline.org/ns/envelope/v1`).
- Mandatory `<from/>` and `<thread/>` (convo_id string, supports hierarchical dot notation for subthreading, e.g., "root.1.research").
- Optional `<to/>` (rare direct routing; most flows use payload namespace/root).
- Exclusive C14N on ingress and egress.
- Malformed XML repaired on ingress; repairs logged in `<huh/>` metadata.

## Identity Injection & Handler Purity
- Handlers are pure, stateless functions with no knowledge of routing, thread context, or their own registered name.
- On ingress (external or gateway messages): <from> is provided and authenticated by the client/gateway (enforced by envelope validation).
- On response generation (after handler execution and multi-payload extraction):
  - The dispatcher injects <from> using the executing listener's registered name (e.g., "calculator.add" or "researcher").
  - For meta/primitive responses: <from> is injected as "core".
- <thread> is inherited from the incoming message (or assigned/updated for primitives like spawn-thread).
- <to> remains optional and rarely used.
- This ensures every enveloped message has a trustworthy, auditable <from> without handler involvement, preventing spoofing and keeping capability code minimal/testable.

## Configuration & Composition
- YAML file (`organism.yaml`) is the bootstrap source of truth, loaded at startup.
- Defines initial listeners, agents, gateways, meta privileges, and OOB channel configuration.
- Runtime structural changes (add/remove listeners, rewire agents, etc.) via local-only privileged commands on the dedicated OOB channel (hot-reload capability).
- No remote or unprivileged structural changes ever.

## Autonomous Schema Layer
- Listeners defined by `@xmlify`-decorated dataclass (payload contract) + pure handler function.
- Mandatory human-readable description string (short "what this does" blurb for tool prompt lead-in).
- Registration (at startup or via hot-reload) automatically generates:
  - XSD cached on disk (`schemas/<name>/v1.xsd`)
  - Example XML
  - Tool description prompt fragment (includes description, params with field docs if present, example input)
- All capability namespaces under `https://xml-pipeline.org/ns/<category>/<name>/v1`.
- Root element derived from payload class name (lowercase) or explicit.

## Message Pump
- Single linear pipeline on main port: ingress → repair → C14N → envelope validation → payload routing.
- Routing key = (payload namespace, root element); unique per listener.
- Meta requests (`https://xml-pipeline.org/ns/meta/v1`) handled by privileged core handler.
- User payloads:
  - Validated directly against listener's cached XSD (lxml)
  - On success → deserialized to typed dataclass instance (`xmlable.from_xml`)
  - Handler called with instance → returns raw bytes (XML fragment, possibly dirty/multi-root)
  - Bytes wrapped in `<dummy></dummy>` → repaired/parsed → all top-level payload elements extracted
  - Each extracted payload wrapped in separate response envelope (inherits thread/from, optional new subthread if primitive used)
  - Enveloped responses buffered and sent sequentially
- Supports single clean response, multi-payload emission (parallel tools/thoughts), and dirty LLM output tolerance.

## Reasoning & Iteration
- LLM agents iterate via open self-calls (same root tag, same thread ID).
- Conversation thread = complete memory and audit trail (all messages logged).
- Subthreading natively supported via hierarchical thread IDs and primitives (e.g., reserved payload to spawn "parent.sub1").
- Optional structured constructs like `<todo-until/>` for visible planning.
- No hidden loops or state machines; all reasoning steps are visible messages.
- Thread management follows the dynamic call tracing model (see thread-management.md). Paths are built by appending target listener names on emission, with automatic popping on responses. Agents remain oblivious, enabling natural delegation and parallelism.

## Security & Sovereignty
- Privileged messages (per `privileged-msg.xsd`) handled exclusively on dedicated OOB channel.
- OOB channel bound to localhost by default (safe for local GUI); separate port/socket from main bus.
- Main MessageBus and pump oblivious to privileged operations — no routing or handling for privileged roots.
- Remote privileged attempts impossible (channel not exposed); any leak to main port logged as security event and dropped.
- Ed25519 identity key used for envelope signing, federation auth, and privileged command verification.
- No agent may modify organism structure, register listeners, or access host resources beyond declared scope.
- “No Paperclippers” manifesto injected as first system message for every LLM-based listener.

## Federation
- Gateways declared in YAML with trusted remote public key.
- Remote tools referenced by gateway name in agent tool lists.
- Regular messages flow bidirectionally; privileged messages never forwarded or accepted.

## Introspection (Meta)
- Controlled via YAML flags (`allow_list_capabilities`, `allow_schema_requests`, etc.).
- Supports `request-schema`, `request-example`, `request-prompt`, `list-capabilities`.
- Remote meta queries optionally allowed per YAML (federation peers).

## Technical Constraints
- Mandatory WSS (TLS) + TOTP on main port.
- OOB channel WSS or Unix socket, localhost-default.
- Internal: lxml trees → XSD validation → xmlable deserialization → dataclass → handler → bytes → dummy extraction.
- Single process, async non-blocking.
- XML is the sovereign wire format; everything else is implementation detail.

## Scheduled Computation
- Timers and delays implemented as normal listeners using async sleeps.
- Caller idles naturally; wakeup messages bubble back via standard tracing.
- Enables recurrent tasks (e.g., periodic monitoring) without blocking or external schedulers.

## Bounded Stateful Listeners
- Pure tools remain stateless.
- Stateful capabilities (e.g., calculator memory, game state) store data per thread path UUID.
- Ensures isolation across conversations, automatic cleanup on idle, and minimal mutable state.
- Handler closes over or receives UUID for access — still oblivious to readable path.

These principles are now locked. All existing docs will be updated to match this file exactly. Future changes require explicit discussion and amendment here first.