# WASM Listeners Specification

Custom listeners can be implemented in WebAssembly (WASM) using AssemblyScript or any language that compiles to WASM. This enables power users to deploy sandboxed, portable handlers.

## Overview

```
User uploads:  handler.wasm + handler.wit
System:        Validates, generates wrappers, registers handlers
Runtime:       Python wrapper calls WASM for compute, handles I/O
```

## Upload Requirements

### 1. WIT File (Required)

The WIT file describes the interface - input/output types for each handler.

```wit
// calculator.wit
package myorg:calculator@1.0.0;

interface calculate {
    record calculate-request {
        expression: string,
    }

    record calculate-response {
        result: f64,
        error: option<string>,
    }
}

interface factorial {
    record factorial-request {
        n: u32,
    }

    record factorial-response {
        result: u64,
    }
}
```

### 2. WASM Module (Required)

The WASM module must export:

| Export | Signature | Description |
|--------|-----------|-------------|
| `handle_{interface}` | `(ptr: i32, len: i32) -> i32` | Handler for each WIT interface |
| `alloc` | `(size: i32) -> i32` | Allocate memory for input |
| `free` | `(ptr: i32) -> void` | Free memory |

**Calling convention:**
- Input: JSON string at `ptr` with length `len`
- Output: Returns pointer to JSON result (length-prefixed or null-terminated)

### 3. Example AssemblyScript

```typescript
// calculator.ts
import { JSON } from "assemblyscript-json";

class CalculateRequest {
    expression: string = "";
}

class CalculateResponse {
    result: f64 = 0;
    error: string | null = null;
    _to: string | null = null;  // Optional: routing target
}

export function handle_calculate(ptr: i32, len: i32): i32 {
    // Parse input
    const input = parseJson<CalculateRequest>(ptr, len);

    // Process
    const response = new CalculateResponse();
    try {
        response.result = evaluate(input.expression);
    } catch (e) {
        response.error = e.message;
    }

    // Return JSON pointer
    return toJsonPtr(response);
}

// Memory management - required exports
const allocations = new Map<i32, i32>();

export function alloc(size: i32): i32 {
    const ptr = heap.alloc(size);
    allocations.set(ptr, size);
    return ptr;
}

export function free(ptr: i32): void {
    if (allocations.has(ptr)) {
        heap.free(ptr);
        allocations.delete(ptr);
    }
}
```

Compile with:
```bash
asc calculator.ts -o calculator.wasm --optimize
```

## Registration Flow

1. **Parse WIT** → Extract interface definitions
2. **Load WASM** → Validate exports match WIT interfaces
3. **Generate wrappers** → Create @xmlify dataclasses from WIT
4. **Register handlers** → Add to listener routing table

```python
# Pseudocode
from xml_pipeline.wasm import register_wasm_listener

register_wasm_listener(
    name="calculator",
    wasm_path="/uploads/calculator.wasm",
    wit_path="/uploads/calculator.wit",
    config={
        "memory_limit_mb": 64,
        "timeout_seconds": 5,
    }
)
```

## Data Flow

```
Message arrives (XML)
    │
    ▼
Python wrapper deserializes to @xmlify dataclass
    │
    ▼
Convert to JSON string
    │
    ▼
Allocate WASM memory, copy JSON
    │
    ▼
Call handle_{interface}(ptr, len)
    │
    ▼
WASM processes synchronously
    │
    ▼
Read result JSON from returned pointer
    │
    ▼
Free WASM memory
    │
    ▼
Convert JSON to @xmlify response dataclass
    │
    ▼
Extract routing target from _to field (if present)
    │
    ▼
Return HandlerResponse
```

## Routing

WASM handlers signal routing via the `_to` field in the response JSON:

```json
{
    "result": 42,
    "_to": "logger"
}
```

- `_to` present → forward to named listener
- `_to` absent/null → respond to caller

## Resource Limits

| Resource | Default | Configurable |
|----------|---------|--------------|
| Memory | 64 MB | Yes |
| CPU time | 5 seconds | Yes |
| Stack depth | WASM default | No |

Exceeding limits results in termination and `SystemError` response.

## Security Model

- **Sandboxed**: WASM linear memory is isolated
- **No I/O**: WASM cannot access filesystem, network, or system
- **No imports**: Host functions are not exposed (pure compute only)
- **Timeout enforced**: Long-running handlers are terminated

For I/O, WASM handlers should:
1. Return a response indicating what I/O is needed
2. Let the agent orchestrate I/O via Python tools
3. Receive I/O results in subsequent messages

## Lifecycle

WASM instances are kept "hot" (loaded) per thread:

- **Created**: On first message to handler in thread
- **Reused**: Subsequent messages in same thread reuse instance
- **Destroyed**: When thread context is pruned (GC)

This amortizes instantiation cost for multi-turn conversations.

## Limitations (v1)

- No streaming responses
- No async/await inside WASM
- No host function imports (pure compute only)
- No direct tool invocation from WASM
- JSON serialization overhead at boundary

## Future Considerations

- WASI support for controlled I/O
- Component Model for richer interfaces
- Streaming via multiple response chunks
- Direct tool imports for trusted WASM
