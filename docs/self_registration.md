# Autonomous Registration & Introspection (v1.3 Preview)

In AgentServer v1.3, manual XSDs, grammars, and LLM tool descriptions are obsolete. Listeners **autonomously generate** their own validation rules and usage prompts at registration time. Introspection (emit-schema/example/prompt) is a privileged core facility — query the organism, not individual listeners.

## The Developer Experience

Declare your input contract as a Python dataclass + a pure handler function. One line to register.

```python
from xmlable import xmlify
from dataclasses import dataclass
from typing import Dict, Any
from xml_pipeline import Listener  # the xmlListener base

# 1. Define the payload "DNA" (@xmlify auto-generates XSD)
@xmlify
@dataclass
class AddPayload:
    """Addition capability."""
    a: int = 0  # First operand
    b: int = 0  # Second operand

# 2. Pure handler: dict[str, Any] -> bytes (response XML fragment)
def add_handler(payload: Dict[str, Any]) -> bytes:
    result = payload["a"] + payload["b"]
    return f"<result>{result}</result>".encode("utf-8")

# 3. Register — autonomous chain reaction begins
add_listener = Listener(
    payload_class=AddPayload,
    handler=add_handler,
    name="calculator.add"  # For discovery/logging
)
bus.register(add_listener)  # <- Boom: XSD, Lark grammar, prompt auto-generated
```

That's it. No XML, no manual schemas. The organism handles the rest.

## Autonomous Chain Reaction on `bus.register()`

When registered, `Listener` (xmlListener base) triggers:

1. **XSD Synthesis**  
   Inspects `@xmlify` dataclass → generates `schemas/calculator.add/v1.xsd` (cached). Namespace derived from module/path (e.g., `https://xml-platform.org/calculator/v1`), root=`add`.

2. **Lark Grammar Transcription**  
   XSD → EBNF grammar string (your dynamic generator). Stored in `listener.grammar` (Lark parser + tree-to-dict transformer). Noise-tolerant: `NOISE* add NOISE*`.

3. **Prompt Synthesis (The "Mente")**  
   From dataclass fields/XSD:  
   ```
   Capability: calculator.add
   Namespace: https://xml-platform.org/calculator/v1
   Root: <add>

   Example:
   <add>
     <a>40</a>
     <b>2</b>
   </add>

   Params: a(int), b(int). Returns: <result>42</result>
   ```  
   Auto-injected into wired agents' system prompts via YAML.

4. **Registry Update**  
   Bus catalogs by `name` and `namespace#root`. Ready for routing + meta queries.

## Introspection: Privileged Meta Facility

Listeners don't "self-register" emit endpoints (no recursion/leakage). Query the **core MessageBus** via reserved `https://xml-platform.org/meta/v1`:

```xml
<envelope ...>
  <payload xmlns="https://xml-platform.org/meta/v1">
    <request-schema>
      <capability>calculator.add</capability>  <!-- name or namespace#root -->
    </request-schema>
  </payload>
</envelope>
```

Bus internal handler:
- Looks up live `Listener` in registry.
- Returns XSD bytes, example XML, or prompt.
- **Privileged**: Admin-only by default (YAML `meta.allow_schema_requests: "admin"`). No upstream topology leaks (A→B→C hides A's full schema).

Other meta ops: `request-example`, `request-prompt`, `list-capabilities`.

## Multi-Handler "Organs"

One logical service, many functions? Register multiples:

```python
subtract_listener = Listener(payload_class=SubtractPayload, handler=subtract_handler, name="calculator.subtract")
bus.register(subtract_listener)  # Independent XSD/grammar/prompt
```

Shared state? Subclass `Listener` escape hatch, pass `handler=self.dispatch`.

## Key Advantages

- **Zero Drift**: Edit dataclass → rerun → XSD/grammar/prompts regenerate.
- **Attack-Resistant**: Lark validates in one noise-tolerant pass → dict → handler.
- **Sovereign Wiring**: YAML agents get live prompts at startup. Downstream sees only wired peers.
- **Federated**: Remote nodes expose same meta namespace (if `meta.allow_remote: true`).

*The tool explains itself to the world. The world obeys the tool.*

