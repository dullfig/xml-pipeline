# aioip — AI Over IP Federation Protocol

**Status:** Design draft (2026-02-08)
**Author:** Daniel + Claude
**Context:** Federation layer for xml-pipeline organisms. Enables secure, capability-negotiated communication between autonomous agent systems.

## Motivation

xml-pipeline organisms are self-contained agent systems. Federation allows them to collaborate: an AgentOS node connects to a Lawrence Livermore organism, authenticates, and its local agents can call remote listeners as if they were local peers. The pump handles routing transparently — agents never know whether a peer is local or remote.

The core insight: **peer tables are capability gates**. The subtract-only ceiling hierarchy already enforces what a caller can touch. aioip extends this across the wire — the server resolves your identity to a peer table and sends you only the listeners you're authorized to see. You literally cannot discover endpoints you don't have access to.

## Design Principles

1. **Envelope-native** — the unit of communication is an XML envelope, not a generic RPC call
2. **Thread-aware** — conversations span organisms; thread chains extend across the wire
3. **Schema-driven** — capabilities are XSD contracts, not just endpoint names
4. **Privilege-scoped** — your peer table determines what you can touch, and it can change mid-connection
5. **Zero-knowledge discovery** — you only see what you're authorized to use; unauthorized endpoints are invisible

## Existing Primitives (Already Built)

| Primitive | Role in Federation |
|-----------|-------------------|
| Ed25519 identity keys | Mutual authentication between organisms |
| TOTP authentication | Second-factor verification on connection |
| Peer tables (subtract-only ceiling) | Capability gate — what a remote caller can touch |
| `<message>` envelope format | Wire format, already protocol-agnostic |
| XSD auto-generation (`@xmlify`) | Capability schema advertisement |
| Opaque thread UUIDs | Agents can't tell if a peer is local or remote |
| Gateway declarations in YAML | Already parsed, just needs runtime behind them |

## Protocol Overview

aioip runs on top of a reliable transport (WebSocket, QUIC, or raw TCP+TLS). The protocol defines four phases: identity exchange, authentication, capability negotiation, and steady-state message flow.

```
┌─────────────┐                          ┌─────────────┐
│  AgentOS     │                          │  Livermore   │
│  (client)    │                          │  (server)    │
└──────┬──────┘                          └──────┬──────┘
       │                                        │
       │  ── Phase 1: IDENTITY ──────────────>  │
       │     Ed25519 pubkey + signed nonce      │
       │  <── Ed25519 pubkey + signed nonce ──  │
       │                                        │
       │  ── Phase 2: AUTHENTICATE ──────────>  │
       │     <totp-auth> token                  │
       │  <── <auth-result> peer-table name ──  │
       │                                        │
       │  <── Phase 3: CAPABILITIES ──────────  │
       │     Filtered listener list + schemas   │
       │  ── <capabilities-ack> ──────────────> │
       │                                        │
       │  ══ Phase 4: READY ══════════════════  │
       │     Envelopes flow bidirectionally     │
       │                                        │
```

## Phase 1: Identity Exchange

Both sides prove identity via Ed25519 challenge-response. This is mutual — the client verifies the server is who it claims, and vice versa.

```xml
<!-- Client → Server -->
<aioip version="1.0">
  <identity>
    <public-key>MCowBQYDK2VwAyEA...</public-key>
    <nonce>a1b2c3d4...</nonce>
    <signature><!-- nonce signed with client private key --></signature>
  </identity>
</aioip>

<!-- Server → Client -->
<aioip version="1.0">
  <identity>
    <public-key>MCowBQYDK2VwAyEA...</public-key>
    <nonce>e5f6g7h8...</nonce>
    <signature><!-- nonce signed with server private key --></signature>
  </identity>
</aioip>
```

Both sides verify the signature against the claimed public key. If either verification fails, the connection is terminated.

**Trust model:** The server maintains a list of known client public keys (see `federation.identities` in YAML). Unknown keys are rejected unless an `allow_unknown` policy is configured (not recommended for production).

## Phase 2: Authentication

After identity verification, TOTP provides a second factor. The server looks up the client's public key and resolves it to a peer table.

```xml
<!-- Client → Server -->
<aioip version="1.0">
  <authenticate>
    <totp-token>482951</totp-token>
  </authenticate>
</aioip>

<!-- Server → Client -->
<aioip version="1.0">
  <auth-result>
    <status>ok</status>
    <peer-table>researcher</peer-table>
    <organism-name>livermore-sim-cluster</organism-name>
    <session-id>a7f3e2b1-...</session-id>
  </auth-result>
</aioip>
```

The `peer-table` field tells the client which privilege tier it has been assigned. This is informational — the server enforces it regardless.

**Failure modes:**
- Unknown public key → `<status>rejected</status> <reason>unknown-identity</reason>`
- Invalid TOTP → `<status>rejected</status> <reason>invalid-totp</reason>`
- Expired TOTP → `<status>rejected</status> <reason>expired-totp</reason>`
- Revoked identity → `<status>rejected</status> <reason>revoked</reason>`

## Phase 3: Capability Exchange

The server sends the client a filtered list of listeners based on the resolved peer table. Only listeners the client is authorized to call are included. The client never sees unauthorized endpoints.

```xml
<!-- Server → Client -->
<aioip version="1.0">
  <capabilities session="a7f3e2b1-...">
    <listener name="simulate.neutron">
      <description>Neutron transport Monte Carlo simulation</description>
      <payload-class>NeutronSimRequest</payload-class>
      <response-class>NeutronSimResult</response-class>
      <schema><![CDATA[
        <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
          <xs:element name="NeutronSimRequest">
            <xs:complexType>
              <xs:sequence>
                <xs:element name="Geometry" type="xs:string"/>
                <xs:element name="Energy" type="xs:double"/>
                <xs:element name="Particles" type="xs:integer"/>
              </xs:sequence>
            </xs:complexType>
          </xs:element>
        </xs:schema>
      ]]></schema>
      <example><![CDATA[
        <NeutronSimRequest>
          <Geometry>cylinder-10cm</Geometry>
          <Energy>14.1</Energy>
          <Particles>1000000</Particles>
        </NeutronSimRequest>
      ]]></example>
      <timeout-seconds>300</timeout-seconds>
    </listener>

    <listener name="data.retrieve">
      <description>Retrieve simulation dataset by ID</description>
      <payload-class>DataRequest</payload-class>
      <response-class>DataResponse</response-class>
      <schema><![CDATA[...]]></schema>
      <timeout-seconds>30</timeout-seconds>
    </listener>
  </capabilities>
</aioip>

<!-- Client → Server -->
<aioip version="1.0">
  <capabilities-ack session="a7f3e2b1-...">
    <accepted>simulate.neutron</accepted>
    <accepted>data.retrieve</accepted>
  </capabilities-ack>
</aioip>
```

**What happens on the client side:**
1. Each remote listener is registered locally as a gateway peer
2. XSD schemas are cached for validation
3. Local agents see remote listeners in their `usage_instructions` (if declared as peers)
4. The pump routes to remote listeners transparently

**Capability refresh:** The server can push updated capabilities mid-session (e.g., after a `modify_peer_table()` call). The client receives a `<capabilities-update>` and adjusts its local registrations.

```xml
<!-- Server → Client (mid-session) -->
<aioip version="1.0">
  <capabilities-update session="a7f3e2b1-...">
    <revoked>simulate.neutron</revoked>
    <reason>Maintenance window</reason>
  </capabilities-update>
</aioip>
```

## Phase 4: Steady-State Message Flow

Once connected, envelopes flow bidirectionally. The client's pump wraps payloads in standard `<message>` envelopes and sends them to the server. The server validates, routes to the target listener, and sends responses back.

```xml
<!-- Client → Server: invoke remote listener -->
<message xmlns="https://xml-pipeline.org/ns/envelope/v1">
  <meta>
    <from>researcher</from>
    <to>simulate.neutron</to>
    <thread>a7f3e2b1-session-thread-uuid</thread>
    <session>a7f3e2b1-...</session>
  </meta>
  <NeutronSimRequest xmlns="">
    <Geometry>cylinder-10cm</Geometry>
    <Energy>14.1</Energy>
    <Particles>1000000</Particles>
  </NeutronSimRequest>
</message>

<!-- Server → Client: response from remote listener -->
<message xmlns="https://xml-pipeline.org/ns/envelope/v1">
  <meta>
    <from>simulate.neutron</from>
    <to>researcher</to>
    <thread>a7f3e2b1-response-thread-uuid</thread>
    <session>a7f3e2b1-...</session>
  </meta>
  <NeutronSimResult xmlns="">
    <KEffective>1.02341</KEffective>
    <Uncertainty>0.00023</Uncertainty>
    <Runtime>142.7</Runtime>
  </NeutronSimResult>
</message>
```

**Thread chain extension:** The thread chain extends across the wire transparently. On the client side, the chain might look like `system.agentos.researcher.livermore:simulate.neutron`. The `livermore:` prefix indicates a remote hop. Agents see only an opaque UUID.

**Envelope signing:** All envelopes are signed with the sender's Ed25519 key. The receiver verifies the signature before processing. This prevents tampering in transit and provides non-repudiation.

## Identity → Peer Table Resolution

The server maintains a mapping from public keys to peer tables. This is the authorization layer.

```
doe-researcher.ed25519.pub  → peer table "researcher"
  → ceiling: [data.retrieve, simulate.neutron, simulate.fusion, compute.allocate]
  → sees 4 listeners

livermore-guest.ed25519.pub → peer table "visitor"
  → ceiling: [data.retrieve, simulate.neutron]
  → sees 2 listeners

ornl-collaborator.ed25519.pub → peer table "collaborator"
  → ceiling: [data.retrieve, simulate.neutron, simulate.fusion]
  → sees 3 listeners
```

The ceiling hierarchy applies: if a `researcher` peer table is a child of `admin`, then researchers can never exceed admin's permissions. Runtime `modify_peer_table()` calls immediately affect connected sessions — a revocation takes effect on the next message, not the next connection.

## YAML Configuration

### Server Side (organism exposing listeners)

```yaml
organism:
  name: "livermore-sim-cluster"
  identity: "config/identity/livermore.ed25519"
  port: 8765

federation:
  listen: true
  bind: "0.0.0.0"
  port: 8770
  protocol: aioip/1.0

  tls:
    cert: "certs/fullchain.pem"
    key: "certs/privkey.pem"
    require_client_cert: false  # Identity verified via Ed25519, not mTLS

  # Map client identities to peer tables
  identities:
    - public_key: "pubkeys/doe-researcher.ed25519.pub"
      peer_table: researcher
      label: "DOE Research Division"
      totp_secret_env: DOE_TOTP_SECRET

    - public_key: "pubkeys/livermore-guest.ed25519.pub"
      peer_table: visitor
      label: "Guest access"
      totp_secret_env: GUEST_TOTP_SECRET

    - public_key: "pubkeys/ornl-collab.ed25519.pub"
      peer_table: collaborator
      label: "ORNL Collaboration"
      totp_secret_env: ORNL_TOTP_SECRET

  # Connection policies
  max_sessions: 100
  session_timeout: 3600          # Seconds of inactivity before disconnect
  allow_unknown_identities: false

peer_tables:
  - name: admin
    entries:
      - listener: simulate.neutron
        peers: []
      - listener: simulate.fusion
        peers: []
      - listener: data.retrieve
        peers: []
      - listener: compute.allocate
        peers: []

  - name: researcher
    parent: admin
    entries:
      - listener: simulate.neutron
        peers: []
      - listener: simulate.fusion
        peers: []
      - listener: data.retrieve
        peers: []
      - listener: compute.allocate
        peers: []

  - name: collaborator
    parent: researcher
    entries:
      - listener: simulate.neutron
        peers: []
      - listener: simulate.fusion
        peers: []
      - listener: data.retrieve
        peers: []

  - name: visitor
    parent: collaborator
    entries:
      - listener: data.retrieve
        peers: []
      - listener: simulate.neutron
        peers: []
```

### Client Side (organism connecting to remote)

```yaml
organism:
  name: "agentos-research-node"
  identity: "config/identity/agentos.ed25519"
  port: 8765

federation:
  # Outbound connections
  peers:
    - name: livermore
      url: "aioip://livermore-sim.llnl.gov:8770"
      identity: "pubkeys/livermore.ed25519.pub"
      totp_secret_env: LIVERMORE_TOTP_SECRET
      auto_connect: true          # Connect on organism boot
      reconnect_interval: 30      # Seconds between reconnection attempts
      prefix: "livermore"         # Local namespace prefix for remote listeners

    - name: argonne
      url: "aioip://argonne-compute.anl.gov:8770"
      identity: "pubkeys/argonne.ed25519.pub"
      totp_secret_env: ARGONNE_TOTP_SECRET
      auto_connect: false         # Connect on demand
      prefix: "argonne"

listeners:
  - name: researcher
    payload_class: agents.researcher.ResearchPayload
    handler: agents.researcher.research_handler
    description: "Research coordinator agent"
    agent: true
    peers:
      - local-calculator           # Local listener
      - livermore:simulate.neutron  # Remote listener (prefix:name)
      - livermore:data.retrieve     # Remote listener
      - argonne:compute.submit      # Remote listener (different organism)
```

**Namespace prefixing:** Remote listeners are prefixed with the federation peer name (`livermore:simulate.neutron`). This avoids name collisions with local listeners and makes the routing boundary explicit in configuration — but agents still see opaque UUIDs at runtime.

## Transport Considerations

### Why Not Just WebSocket?

WebSocket works fine as a carrier. aioip is the semantic layer on top — it defines:
- **Handshake** — identity + TOTP + capability negotiation (WebSocket has none of this)
- **Capability filtering** — zero-knowledge endpoint discovery
- **Session management** — reconnection, capability refresh, graceful shutdown
- **Back-pressure** — per-listener flow control (not just TCP windowing)
- **Thread migration** — moving a conversation to a different organism mid-flight

### Candidate Transports

| Transport | Pros | Cons |
|-----------|------|------|
| WebSocket + TLS | Widely supported, firewall-friendly | No multiplexing, head-of-line blocking |
| QUIC | Multiplexed streams, 0-RTT reconnect | Less firewall-friendly, newer |
| Raw TCP + TLS | Simplest, lowest overhead | Need own framing |

**Recommendation:** Start with WebSocket + TLS (already have `websockets` dependency). Migrate to QUIC later if multiplexing becomes a bottleneck.

### Framing

Over WebSocket, each aioip message is a single WebSocket text frame containing the XML. Over raw TCP, use a simple length-prefix framing:

```
[4 bytes: message length (big-endian uint32)][N bytes: XML payload]
```

## Security Model

### Defense in Depth

1. **TLS** — transport encryption (man-in-the-middle protection)
2. **Ed25519 identity** — mutual authentication (impersonation protection)
3. **TOTP** — second factor (stolen key protection)
4. **Peer table filtering** — capability gate (authorization)
5. **Envelope signing** — per-message integrity (tampering protection)
6. **Opaque thread UUIDs** — topology hiding (information leakage protection)

### What a Compromised Client Cannot Do

- **Discover unauthorized endpoints** — capability exchange only shows what's in your peer table
- **Forge identity** — Ed25519 signatures are verified on every message
- **Escalate privileges** — peer tables are subtract-only; runtime modifications can only revoke
- **See internal topology** — thread chains are behind opaque UUIDs; the client doesn't know how `simulate.neutron` is implemented internally
- **Replay messages** — nonces in the handshake prevent replay; TOTP tokens expire

### What a Compromised Server Cannot Do to a Client

- **Inject messages into the client's local pump** — the client validates all incoming envelopes against expected response schemas
- **Impersonate a different server** — the client verifies the server's Ed25519 identity against a pinned public key

## Back-Pressure and Flow Control

Per-listener flow control allows the server to throttle specific capabilities independently.

```xml
<!-- Server → Client: slow down neutron requests -->
<aioip version="1.0">
  <flow-control session="a7f3e2b1-...">
    <listener name="simulate.neutron">
      <max-inflight>2</max-inflight>
      <reason>Cluster at 95% capacity</reason>
    </listener>
  </flow-control>
</aioip>
```

The client's pump respects `max-inflight` by queuing excess requests locally. This integrates naturally with the existing `agent_semaphores` rate-limiting.

## Thread Migration (Future)

A server can signal that a conversation should continue on a different organism:

```xml
<!-- Server → Client: migrate this thread -->
<aioip version="1.0">
  <thread-migrate session="a7f3e2b1-...">
    <thread-id>uuid-of-active-thread</thread-id>
    <target-url>aioip://overflow-cluster.llnl.gov:8770</target-url>
    <target-identity>pubkeys/overflow.ed25519.pub</target-identity>
    <migration-token>signed-token-for-target</migration-token>
    <reason>Primary cluster full, routing to overflow</reason>
  </thread-migrate>
</aioip>
```

The client opens a new connection to the target, presents the migration token, and resumes the thread. The original server's thread state is transferred out-of-band between servers.

**Note:** This is a future capability. Initial implementation should focus on static connections.

## Implementation Roadmap

### Phase 1: Core Protocol (MVP)
- [ ] Protocol framing over WebSocket
- [ ] Ed25519 mutual identity exchange
- [ ] TOTP authentication
- [ ] Server-side identity → peer table resolver
- [ ] Capability exchange (filtered listener advertisement)
- [ ] Gateway listener registration on client side
- [ ] Envelope forwarding (client → server → listener → server → client)
- [ ] Thread chain extension across wire boundary

### Phase 2: Robustness
- [ ] Session management (timeout, reconnection, keepalive)
- [ ] Capability refresh (mid-session peer table changes)
- [ ] Per-listener back-pressure / flow control
- [ ] Connection pooling for high-throughput scenarios
- [ ] Graceful shutdown (drain in-flight requests)

### Phase 3: Advanced
- [ ] QUIC transport option
- [ ] Thread migration between organisms
- [ ] Multi-hop routing (organism A → organism B → organism C)
- [ ] Capability caching (avoid re-negotiation on reconnect)
- [ ] Federation mesh discovery (organisms announce to a registry)

## Relationship to AgentOS

AgentOS is a network of organisms that federate via aioip. Each AgentOS node:
1. Runs a local organism with its own listeners (tools, agents)
2. Connects to remote organisms via aioip federation peers
3. Local agents see remote listeners as peers in their `usage_instructions`
4. The pump routes messages transparently — local and remote are indistinguishable

The `federation.peers` YAML section declares outbound connections. On boot (if `auto_connect: true`), the organism connects, authenticates, negotiates capabilities, and registers remote listeners locally. Agents can immediately start calling them.

```
AgentOS Node                    Livermore Cluster
┌───────────────────┐           ┌───────────────────┐
│  researcher agent │           │  simulate.neutron  │
│  local-calculator │ ←aioip→  │  simulate.fusion   │
│  local-search     │           │  data.retrieve     │
│                   │           │  compute.allocate   │
│  livermore:sim.*  │ (gateway) │                     │
│  livermore:data.* │ (gateway) │                     │
└───────────────────┘           └───────────────────┘

researcher's peers: [local-calculator, livermore:simulate.neutron, livermore:data.retrieve]
                     ↑ local                ↑ remote (transparent to agent)
```

## LLM Context Integration: Just-in-Time Tool Loading

### The Problem

When an LLM agent calls a federation tool, the remote organism returns new capabilities — listeners the agent has never seen before. The LLM has no `usage_instructions` for these remote tools. It doesn't know the schemas, the field names, or what the tools do. Static pre-loading of all possible remote schemas is wasteful, potentially stale, and breaks the zero-knowledge principle.

### The Solution: Capability Schemas in the Response Payload

The `federation.connect` handler returns capability documentation as its response payload. This response enters the context buffer via `.respond()`, and the LLM sees it on the next turn. It stays in context for the thread's lifetime — exactly where and when the LLM needs it.

```
Agent (LLM)                  federation.connect           Livermore
    │                              │                          │
    │  Question("connect to        │                          │
    │   livermore for neutron sim") │                          │
    │  ───────────────────────>    │                          │
    │                              │  aioip 4-phase handshake │
    │                              │  ──────────────────────> │
    │                              │  <── capabilities ────── │
    │                              │                          │
    │  <── FederationResult ────── │                          │
    │      .endpoints = [                                     │
    │        {name: "livermore:simulate.neutron",              │
    │         description: "Neutron transport Monte Carlo",   │
    │         schema: "<NeutronSimRequest>                    │
    │           <Geometry>string</Geometry>                   │
    │           <Energy>double</Energy>                       │
    │           <Particles>integer</Particles>                │
    │         </NeutronSimRequest>",                          │
    │         example: "<NeutronSimRequest>                   │
    │           <Geometry>cylinder-10cm</Geometry>            │
    │           <Energy>14.1</Energy>                         │
    │           <Particles>1000000</Particles>                │
    │         </NeutronSimRequest>"}                          │
    │      ]                                                  │
    │      .message = "Connected to livermore-sim-cluster.    │
    │       2 endpoints available."                           │
    │                                                         │
    │  ── (LLM now has schemas in context) ──                │
    │                                                         │
    │  NeutronSimRequest(                                     │
    │    Geometry="cylinder-10cm",                            │
    │    Energy=14.1,                                         │
    │    Particles=1000000)                                   │
    │  ───────────────────────> (routed to livermore) ──────> │
    │                                                         │
    │  <── NeutronSimResult ──────────────────────────────── │
    │      KEffective=1.02341                                 │
    │      Uncertainty=0.00023                                │
```

### How It Works Mechanically

1. **Agent's static peers** include `federation` (a local tool-listener)
2. **Agent calls** `federation.connect` with target organism name
3. **Federation handler** performs the aioip handshake:
   - Opens connection (or reuses existing session)
   - Authenticates (Ed25519 + TOTP)
   - Receives filtered capabilities based on peer table
   - Registers remote listeners as gateway peers in the local pump
   - **Modifies the thread's peer table** to grant the agent access to the new remote listeners
4. **Handler responds** with `FederationResult` containing the schema documentation
5. **Context buffer** receives the response — the LLM sees the schemas on its next turn
6. **Peer table** now includes the remote listeners — the pump allows routing to them
7. **Agent constructs messages** using the schemas it just learned, sends them normally
8. **Pump routes transparently** — the gateway peer forwards envelopes over the aioip connection

### Peer Table Dynamics

The critical piece: when `federation.connect` succeeds, the handler must update the thread's peer table to include the negotiated remote listeners. Without this, the pump's peer enforcement would block the agent from calling them.

```python
async def handle_federation_connect(payload: FederationConnect, metadata: HandlerMetadata):
    # ... aioip handshake, get capabilities ...

    # Register remote listeners as gateway peers in the pump
    for cap in capabilities:
        pump.register_gateway(f"{prefix}:{cap.name}", remote_session, cap.schema)

    # Grant this thread access to the new remote peers
    remote_peers = [f"{prefix}:{cap.name}" for cap in capabilities]
    pump.modify_peer_table(
        table_name,              # This thread's peer table
        metadata.own_name,       # The agent that called us
        grant=remote_peers,      # Add remote listeners to allowed peers
    )

    # Return schemas to the LLM via context buffer
    return HandlerResponse.respond(
        payload=FederationResult(
            organism=remote_name,
            endpoints=[...schema docs...],
            message=f"Connected. {len(capabilities)} endpoints available.",
        )
    )
```

**Wait — ceiling enforcement.** `modify_peer_table(..., grant=...)` validates against the ceiling. The remote peers wouldn't exist in the ceiling (they weren't in the original YAML `listener.peers`).

Two solutions:

**Option A: Pre-declare federation wildcard in peers.** The agent's YAML config includes a wildcard or federation namespace:
```yaml
peers:
  - local-calculator
  - "livermore:*"        # Ceiling allows anything from livermore
```
The ceiling contains the wildcard; `modify_peer_table` grants specific endpoints within it.

**Option B: Federation peers bypass ceiling.** Federation-granted peers are a separate privilege class — they come from a cryptographically authenticated source (the remote organism's filtered capability exchange), not from the local agent requesting them. The pump could have a `grant_federation_peer()` method that doesn't check the static ceiling.

Option B is cleaner — the federation handshake IS the authorization. The remote organism already filtered capabilities through the peer table. Double-gating through the local ceiling adds complexity without security value.

### Thread Scope

The federation connection is thread-scoped:
- The schemas are in THIS thread's context buffer
- The peer table grants are for THIS thread
- When the thread terminates, `_cleanup_thread()` can optionally close the federation session
- A different thread (different user, different privilege tier) gets its own `federation.connect` call, its own capability negotiation, its own schemas

This means two agents in the same organism can have different views of the same remote organism — one might see 4 endpoints, the other might see 2 — depending on which peer table their identity resolves to on the remote side.

### Lazy vs Eager Connection

Two patterns for when the connection happens:

**Lazy (LLM-driven):** The agent decides when to connect. It calls `federation.connect("livermore")` as part of its reasoning. This is the pattern described above — just-in-time, thread-scoped.

**Eager (boot-time):** The organism connects on startup (`auto_connect: true` in YAML). Remote listeners are pre-registered. The schemas are included in the agent's static `usage_instructions`. The LLM sees remote tools from the first turn.

Both patterns have value:
- **Lazy** for on-demand connections, dynamic target selection, thread-scoped isolation
- **Eager** for always-available infrastructure (your org's compute cluster, shared data services)

They're not mutually exclusive. An organism can have eager connections to known infrastructure and lazy connections to on-demand resources.

## Open Questions

1. **TOTP per-identity or per-organism?** Currently sketched as per-identity (`totp_secret_env` per identity entry). Could also be per-organism (single TOTP for all connections to a given server).

2. **Bidirectional capabilities?** Current design is client→server (client calls server's listeners). Should the server also be able to call the client's listeners? This enables push-based patterns but complicates the security model.

3. **Schema versioning?** What happens when a remote listener's schema changes? The client has cached XSDs from capability exchange. Need a version field or hash to detect staleness.

4. **Rate limiting scope?** Should `max_inflight` be per-session, per-identity, or per-listener? Probably per-listener-per-session, with a global cap per-identity.

5. **Protocol version negotiation?** The `<aioip version="1.0">` wrapper allows future protocol versions. Need to define a version negotiation mechanism for backward compatibility.

6. **Federation peer ceiling bypass (Option A vs B)?** Should federation-granted peers go through the local ceiling (`livermore:*` wildcard in YAML) or bypass it entirely (federation handshake is its own authorization)? Option B is simpler but means the local admin can't cap what a remote organism offers. Option A gives the local admin a veto.
