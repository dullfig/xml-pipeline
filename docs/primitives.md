# AgentServer v2.1 — System Primitives (Magic Tags)

These payload root elements receive special routing and/or side effects in the message pump.  
They reside in the reserved namespace `https://xml-pipeline.org/ns/core/v1`.

## `<huh>`
### `<huh>`
- Emitted exclusively by the system
- Routes back to the listener that triggered the error
- Payload structure:
  ```xml
  <huh>
    <error>Brief canned error message (e.g., "Invalid payload structure")</error>
    <original-attempt>Base64-encoded raw bytes of the failed attempt (truncated if large)</original-attempt>
  </huh>
  ```
- Purpose: Safe, LLM-friendly diagnostic feedback
- Security note: Error messages are abstract and canned — no raw validator output is exposed to agents
- Security note:
  - Certain classes of errors (payload schema violations, unknown root tags, etc.) are intentionally reported with identical abstract messages.
  - This prevents topology probing: an agent or external caller cannot distinguish between "wrong schema for existing capability" and "capability does not exist".
  - Authorized introspection is available only via controlled meta queries.

## `<todo-until>`
- May be emitted by any listener
- Routes to self (uses the emitting listener's unique root tag mechanism)
- No side effects
- Purpose: Optional visible scaffolding for structured reasoning and iteration planning

## `<return>`
- May be emitted by any listener
- Routes to the immediate parent listener in the private thread hierarchy
- Side effect: The Current subthread below the current listener is pruned after successful delivery of message.<br>the current thread tail is the current listener.
- Purpose: Explicit return-to-caller semantics with automatic cleanup

## `<halt>`
- May be emitted by any listener
- Routes to the immediate parent listener in the private thread hierarchy
- Side effect: The Entire thread is pruned up to and including the current listener.<br>the current thread tail is the parent listener.
- Purpose: Explicit termination of the current thread and all its subthreads