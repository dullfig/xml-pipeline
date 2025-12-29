# Position Paper: Privileged Messages Are Local-Only

**Project**: xml-pipeline  
**Date**: December 28, 2025  
**Author**: dullfig (organism owner)

### Principle

In the xml-pipeline organism, **all privileged messages are strictly local**. No privileged message may ever originate from or be accepted from a remote source, even in the presence of federation.

### Definition

A privileged message is any message wrapped in the `<privileged-msg>` envelope defined in `privileged-msg.xsd`, carrying an Ed25519 signature from the organism’s permanent private key. These messages are the sole mechanism for structural change (register/unregister listeners and gateways, manage LLM providers, shutdown, etc.).

### Rationale

1. **Cryptographic Sovereignty**  
   The organism’s permanent Ed25519 private key is the root of all structural authority. It is generated offline, stored securely, and used only by the human owner to sign privileged commands. It is never exposed to the network.

2. **Trust Boundary Separation**  
   Federation enables bounded intelligence sharing between organisms. Remote organisms are trusted for regular message flow within pre-approved capability contracts, but are **never** trusted for structural control. Allowing remote privileged messages would collapse this boundary.

3. **Attack Surface Reduction**  
   Even a correctly signed privileged message arriving from a remote gateway represents either:
   - A compromised private key (catastrophic, but contained to local scope), or
   - A replay/interception attack, or
   - A misconfigured or malicious remote organism.  
   In all cases, the only safe response is immediate silent drop.

4. **No Legitimate Use Case**  
   There is no conceivable legitimate scenario in which one organism should send structural commands to another. Federation is for collaborative computation, not remote administration.

### Enforcement

- The federation gateway listener (`RemoteGatewayListener` or equivalent) **must** drop any inbound message whose root tag is `<privileged-msg>`, regardless of signature validity.
- Such drops are logged as security events with full context (source gateway, convo_id, timestamp).
- Outbound privileged messages are never forwarded to remote gateways (symmetric rule).
- Documentation and code comments clearly state: "Privileged messages are local-only by design."

### Conclusion

This rule is non-negotiable and constitutive of the organism’s security model. It ensures that cryptographic sovereignty remains exclusively with the human owner, even as the organism grows to federate with others. Remote collaboration is powerful; remote control is forbidden.

This principle will be enshrined in code, documentation, and all future federation designs.

— dullfig  
Owner, xml-pipeline organism
