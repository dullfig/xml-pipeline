# AgentServer — The Living Substrate (v1.2)
**December 30, 2025**  
**Architecture: Turing-Complete, Stack-Managed Multi-Agent Organism**

## What It Is
AgentServer is a production-ready "body" for the `xml-pipeline` nervous system. It is a secure, XML-native substrate hosting concurrent, stateful agents (organs) sharing a unified, tamper-proof **MessageBus**. 

Version 1.2 introduces **Computational Completeness**, enabling agents to manage complex state through recursive iteration and isolated threading, all governed by a strict resource stack.

## Core Philosophy
- **Computational Sovereignty:** The platform is Turing-complete via `<todo-until/>` (loops) and `<start-thread/>` (branching) primitives.
- **Multimodal Routing:** 
    - **Directed Mode:** Targeting a specific organ via `<to/>` initiates a stack-managed, roll-up lifecycle.
    - **Broadcast Mode:** Omitting `<to/>` allows for organic, parallel response from all listeners of a root tag.
- **Need-to-Know Topology:** Wiring is defined via YAML. Agents are only aware of the peers explicitly listed in their configuration; calling conventions are auto-injected into prompts at runtime.
- **No Magic Backchannels:** Even system-level notifications must wear an XML envelope and flow through the bus to reach agents.

## Key Features

### 1. The Stack-Based Lifecycle
- **UUID Propagation:** UUID v4 thread identifiers are propagated to the deepest leaf tool. A thread remains on the **Stack** until all leaves respond and "roll up" to the parent.
- **Physical Bounding:** The maximum stack depth is configurable via the YAML BIOS, providing a "Gas Limit" against infinite loops.

### 2. The Sovereign Logger (The Witness)
- **Inline Auditing:** Positioned immediately after XML repair, the Logger witnesses all traffic before routing.
- **The Confessional:** Agents can write inner reasoning or state snapshots via the `<logger/>` tag.
- **Write-Only Law:** The Logger is physically incapable of responding with data. Agents can "vent" to the record, but they can never read from it, preventing rogue memory or lateral state leakage.

### 3. Isolated Structural Management (OOB)
- **Out-of-Band Control:** Structural commands (registration, wiring, shutdown) use a dedicated secure port and are validated via site-specific Ed25519 signatures.
- **Handshake of Death:** Graceful shutdown requires a direct handshake between the AgentServer and the Logger, ensuring all states are flushed before exit.

### 4. The Immune System (`repair_and_canonicalize`)
- **Scar Tissue (`<huh/>`):** Structural fixes are immortalized in the message metadata, providing a transparent audit log and diagnostic feedback loop for LLMs.

## Technical Stack
- **Protocol:** Mandatory WSS (TLS) + TOTP 2FA.
- **Identity:** Ed25519 signatures (OOB) + UUID v4 (In-Bus).
- **Format:** `lxml` trees (Internal) / Exclusive C14N (External).

## Why This Matters
AgentServer is a **Secure Virtual Machine for Intelligence.** It provides the freedom of Turing-complete reasoning within the absolute safety of a hardened, owner-controlled skeletal structure.

**One port. Many bounded minds. Total sovereignty.** 🚀

---
*XML wins. Safely. Permanently.*