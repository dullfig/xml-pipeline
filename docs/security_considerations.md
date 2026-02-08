# Security Considerations

The agentserver controls AI swarms that can make LLM calls, process data, and potentially interact with external systems. Security is critical.

## Threat Model

**What are we protecting?**
- Agent prompts (intellectual property, sensitive instructions)
- Message content (potentially contains PII, business data)
- LLM API keys (expensive, abuse potential)
- Control plane (unauthorized injection, killing threads, config changes)

**Who are the adversaries?**
- External attackers (internet-facing API)
- Malicious insiders (authorized users with bad intent)
- Compromised agents (prompt injection leading to unintended behavior)

---

## Authentication

### Options

| Method | Pros | Cons |
|--------|------|------|
| **TOTP (2FA)** | Simple, no external deps, widely understood | Requires secure enrollment, backup codes |
| **WebAuthn/Passkeys** | Phishing resistant, no shared secrets | Browser support varies, device-bound |
| **mTLS (client certs)** | Strong, good for server-to-server | Certificate management complexity |
| **OAuth/OIDC** | Delegate to IdP (Google, Okta, etc.) | External dependency, complexity |
| **API Keys** | Simple for programmatic access | No 2FA, easy to leak |

### Recommendation

Layered approach:
1. **Human users** - Password + TOTP (minimum), WebAuthn (preferred)
2. **Programmatic access** - API keys with IP allowlisting + rate limits
3. **Server-to-server** - mTLS or signed JWTs

### Session Management

- Short-lived access tokens (15 min)
- Longer refresh tokens (secure storage only)
- Force re-auth for sensitive operations (config changes, agent control)
- Session invalidation on password change

---

## Authorization

### Role-Based Access Control (RBAC)

| Role | Capabilities |
|------|-------------|
| **Viewer** | Read topology, watch message flow, view threads |
| **Operator** | Inject messages, view agent configs |
| **Admin** | Pause/resume agents, kill threads, reload config |
| **Owner** | Edit configs, manage users, view secrets |

### Resource-Level Permissions

- Per-agent permissions (can user X inject to agent Y?)
- Per-thread visibility (some threads may be restricted)
- Config sections (prompts may be more sensitive than routing)

### Peer Tables (Dynamic Authorization)

Peer tables provide thread-scoped, mutable authorization enforcement:

- **Named tables** (e.g., `premium`, `basic`) define per-listener peer mappings that override static `Listener.peers`
- **Thread-scoped**: table name embedded in thread chain prefix (behind opaque UUID), invisible to agents
- **Mutable at runtime**: modifying a table immediately affects all threads using it — enables mid-conversation privilege revocation
- **Dispatch-time enforcement**: the pump re-reads table contents on every message (no caching, no stale grants)
- **Managed via OOB**: `register-peer-table` and `modify-peer-table` privileged commands, or programmatic API

This enables use cases like:
- Premium vs basic user tiers with different tool access
- Revoking a capability from a running conversation without restart
- A/B testing different permission sets across concurrent threads

### Principle of Least Privilege

- Default deny
- GUI gets only what it needs to render
- Operators can't see raw prompts unless explicitly granted
- Peer tables should grant the minimum set of peers needed per tier

---

## Transport Security

### Requirements

- **TLS 1.3** minimum (no fallback to older versions)
- **HSTS** headers for browser clients
- **Certificate pinning** for native/mobile clients (optional)
- **No mixed content** - WebSocket must be WSS

### Internal Traffic

- Agent-to-agent within organism: localhost only, no auth needed
- Cross-organism: mTLS required
- LLM API calls: TLS required, key rotation

---

## Secrets Management

### Types of Secrets

| Secret | Storage | Rotation |
|--------|---------|----------|
| LLM API keys | Env vars or vault | Monthly |
| User passwords | Hashed (argon2id) | On demand |
| TOTP seeds | Encrypted at rest | On re-enrollment |
| Session tokens | Memory/Redis | Short-lived |
| Agent prompts | Config files | Version controlled |

### Never Expose

- Raw API keys in API responses
- Password hashes
- TOTP seeds
- Full prompts (unless authorized)

### Sanitization

API responses should scrub:
- `api_key_env` values → show env var name, not value
- Prompt content → show hash or "hidden" unless authorized
- Message payloads → optionally redact PII

---

## Audit Logging

### What to Log

| Event | Data |
|-------|------|
| Auth success/failure | User, IP, timestamp, method |
| Config changes | User, before/after hash, timestamp |
| Control actions | User, action, target, timestamp |
| Message injection | User, target agent, thread ID |
| Agent errors | Agent, error type, thread ID |

### Log Security

- Logs must not contain secrets
- Tamper-evident (signed, append-only)
- Retained for compliance period
- Accessible only to auditors

---

## Prompt Injection Defense

Agents are vulnerable to prompt injection via message content.

### Mitigations

1. **Input validation** - Schema enforcement on payloads
2. **Prompt isolation** - System prompt separate from user content
3. **Output filtering** - Detect/block suspicious responses
4. **Sandboxing** - Agents can't access resources beyond their scope
5. **Rate limiting** - Prevent rapid-fire injection attempts

### Monitoring

- Flag unusual patterns (agent talking to unexpected peers)
- Alert on error spikes
- Log full message history for forensics

---

## Rate Limiting

### API Limits

| Endpoint | Limit |
|----------|-------|
| `/inject` | 10/min per user |
| `/ws` connections | 5 per user |
| WebSocket events | 100/sec aggregate |
| Auth attempts | 5/min per IP |

### Backpressure

- Queue depth limits per agent
- Thread count limits per user
- Graceful degradation under load

---

## Open Questions

- [ ] Single-tenant or multi-tenant?
- [ ] Self-hosted only or SaaS option?
- [ ] Compliance requirements? (SOC2, HIPAA, GDPR)
- [ ] Should users be able to edit prompts via GUI?
- [ ] How to handle agent-to-external-API credentials?
- [ ] Disaster recovery / backup strategy?
- [ ] Penetration testing plan?

---

## Implementation Checklist

- [ ] TLS configuration hardened
- [ ] Auth system implemented (password + TOTP minimum)
- [ ] RBAC roles defined and enforced
- [ ] API rate limiting in place
- [ ] Secrets never logged or exposed
- [ ] Audit logging enabled
- [ ] Input validation on all endpoints
- [ ] Session management secure
- [ ] Security headers set (HSTS, CSP, etc.)
- [ ] Dependency scanning (CVE alerts)
