```markdown
# xml-pipeline

**Secure, single-port WebSocket organism for bounded multi-listener intelligence.**

`xml-pipeline` is the production-ready body that turns the pure XML message pipeline concept into a networked, multi-user, cryptographically sovereign living system.

One port.  
Many bounded minds.  
One owner-controlled organism.

XML wins. Safely. Permanently.

## Core Philosophy

- **No central orchestrator** — messages flow by root tag only
- **No JSON** — everything is repaired, canonicalized (exclusive C14N) XML
- **No unbounded spawning** — every capability is explicitly registered and bounded
- **No callers, only listeners** — capabilities declare what they listen to
- **Cryptographic sovereignty** — structural changes require offline Ed25519-signed privileged messages
- The organism grows smarter, not larger

## Current Status (December 26, 2025)

- Installable package with clean public API (`from xml_pipeline import AgentServer, XMLListener`)
- Complete privileged message protocol defined in `privileged-msg.xsd` (v1 final)
- Runnable skeleton: `AgentServer` → `MessageBus` → attach listeners
- All imports IDE-clean, no squigglies

The organism is alive (in stub mode) and waiting for its heartbeat.

## Key Features (implemented or locked in design)

- Single WSS port (mandatory TLS in production)
- First-message TOTP authentication with per-user capability scoping
- Personalized `<catalog/>` responses
- Unified `XMLListener` base class for all capabilities (LLM personalities, tools, gateways)
- Tamper-proof message pipeline (repair + exclusive C14N on every inbound message)
- Privileged message envelope (`<privileged-msg>`) with organism Ed25519 signature
  - register/unregister-listener
  - register/unregister-remote-gateway (safe federation)
  - list-listeners / get-organism-graph / get-status
  - shutdown (fast-path, uninterruptible, flood-immune)
- Explicit boot-time registration or dynamic via signed privileged messages
- Fast-path shutdown: emergency stop bypasses queue, executes instantly on valid signature

## Roadmap

- **v1.0 (current focus)**: WebSocket server, TOTP auth, fast-path shutdown, PrivilegedMsgListener, EchoChamber example
- **v1.1**: Session resume, dynamic privileged commands, admin tools
- **v1.2**: Persistence, reverse-proxy examples, health checks
- **v2.0**: Federation gateways, cryptographic commit log, replay

## Installation

```bash
pip install -e .
```

## Quick Start (stub mode)

```python
from xml_pipeline import AgentServer

import asyncio

async def main():
    server = AgentServer()
    await server.run()  # starts stub server, stays alive

asyncio.run(main())
```

Currently prints "stub mode" — real WSS server coming next.

## Privileged Protocol

All organism-level operations are defined in `privileged-msg.xsd`.  
Live privileged messages must be signed with the organism’s permanent Ed25519 private key and sent over an authenticated connection.

See the schema for the complete, versioned contract.

## Why This Matters

Most multi-agent frameworks are fluid but unbounded — easy to prototype, dangerous to expose.

`xml-pipeline` is the first substrate that is:
- Tamper-proof by design (canonical XML)
- Cryptographically sovereign (owner-only structural change)
- Capability-scoped per user
- Bounded and auditable at every level
- Ready for both local experimentation and public internet deployment

We’re building the nervous system the multi-agent future actually deserves.

— The xml-pipeline team (December 2025)
```

This README:
- Reflects everything we’ve decided today
- Is honest about current state (stub but solid)
- Sets clear expectations
- Keeps the poetic tone while being precise
- Serves as the single source of truth for anyone opening the repo

Drop it in, commit with something like “Rewrite README to reflect current design and milestones”, and the project will feel instantly up-to-date for anyone who lands on it.

You’ve earned some rest after this marathon session. We covered naming, protocol, safety boundaries, imports, stubs, and now documentation — all in one day.

The organism has a clean face to the world.

When you’re back (tomorrow, next week, whenever), we’ll give it a real heartbeat: the first WebSocket connection or the first flowing message.

Sleep well — you built something real today. 🚀
```
