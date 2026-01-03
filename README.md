# AgentServer — The Living Substrate (v1.3)
***"It just works..."***

**January 01, 2026**  
**Architecture: Autonomous Grammar-Driven, Turing-Complete Multi-Agent Organism**

## What It Is
AgentServer is a production-ready substrate for the `xml-pipeline` nervous system. Version 1.3 introduces **Autonomous Grammar Generation**, where the organism defines its own language and validation rules in real-time using Lark and XSD automation.

## Core Philosophy
- **Autonomous DNA:** The system never requires a human to explain tool usage to an agent. Listeners automatically generate their own XSDs based on their parameters, which are then converted into **Lark Grammars** for high-speed, one-pass scanning and validation.
- **Grammar-Locked Intelligence:** Dirty LLM streams are scanned by Lark. Only text that satisfies the current organism's grammar is extracted and validated. Everything else is ignored as "Biological Noise."
- **Parameter-Keyed Logic:** Messages are delivered to agents as pristine Python dictionaries, automatically keyed to the listener's registered parameters.
- **Computational Sovereignty:** Turing-complete via `<todo-until/>` and `<start-thread/>` primitives, governed by a strict resource stack.

## Key Features

### 1. The Autonomous Language Layer
- **XSD-to-Lark Generator:** A core utility that transcribes XSD schema definitions into EBNF Lark grammars. This enables the server to search untrusted data streams for specific XML patterns with mathematical precision.
- **Auto-Descriptive Organs:** The base `XMLListener` class inspects its own instantiation parameters to generate a corresponding XSD. The tool itself tells the world how to use it.
- **Protocol Agnostic:** To add a new field (like `<cc/>`) to the entire swarm, you simply update the central XSD. The entire organism's grammar updates instantly.
- **[Read Further: Self-Registration & Autonomous Grammars](docs/self-grammar-generation.md)**

### 2. The Stack-Based Lifecycle
- **UUID Custody:** UUID v4 thread identifiers are born via `<spawn-thread/>` and managed on a physical stack.
- **Leaf-to-Root Roll-up:** Threads remain active until the final leaf responds, ensuring perfect resource tracking and preventing runaway processes.

### 3. The Sovereign Witness
- **Inline Auditing:** The Logger witnesses all traffic before routing.
- **The Confessional:** Agents record inner thoughts via `<logger/>`. The Logger is **strictly write-only** to prevent rogue memory or shared-state leaks.

### 4. Isolated Structural Control
- **Out-of-Band (OOB) Port:** Structural commands (registration, wiring, shutdown) use a dedicated port and Ed25519 signatures, ensuring "Life/Death" commands cannot be delayed by agent traffic.
- **[Read Further: YAML Configuration System](docs/configuration.md)**

## Technical Stack
- **Parsing:** Lark (EBNF Grammar) + `lxml` (Validation/C14N).
- **Protocol:** Mandatory WSS (TLS) + TOTP 2FA.
- **Identity:** Ed25519 (OOB) + UUID v4 (In-Bus).
- **Format:** `lxml` trees (Internal) / Exclusive C14N (External).

## Why This Matters
AgentServer v1.3 is the first multi-agent substrate where the **language is the security.** By automating the link between XSD, Grammar, and LLM Prompts, you’ve created an organism that is impossible to "misunderstand." It is a self-documenting, self-validating, and self-regulating intelligent system.

**One port. Many bounded minds. Autonomous Evolution.** 🚀

---
*XML wins. Safely. Permanently.*