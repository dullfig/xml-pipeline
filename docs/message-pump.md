# Message Pump — End-to-End Flow (v2.0)

The AgentServer message pump processes individual messages through a single, linear, attack-resistant pipeline. The outer dispatcher runs a continuous async loop, draining per-thread message buffers (queues) until empty — enabling persistent, branched reasoning without artificial limits.

```mermaid
flowchart TD
    subgraph Ingress
        A[Raw Bytes] --> B[Repair + C14N]
        B --> C[Enqueue to Thread Queue]
    end

    subgraph DispatcherLoop
        D[Dequeue Next Message] --> E{Envelope Valid?}
        E -->|No| F[Discard / System Error]
        E -->|Yes| G{Payload Namespace?}
        G -->|Meta| H["Core Handler<br>(raw payload)"]
        G -->|Normal| I[Validate Payload<br>vs Cached XSD]
        I -->|Fail| F
        I -->|Pass| J["Deserialize to<br>@xmlify Dataclass"]
        J --> K["Call Handler<br>(typed instance → bytes)"]
        H --> L["Wrap bytes in<br>&ltdummy&gt&lt/dummy&gt"]
        K --> L
    end

    subgraph Response
        L --> M[Repair + Extract<br>Multi-Payloads]
        M --> N{Extracted Payloads?}
        N -->|0| O["Optional: Inject<br>&ltagent-error&gt or Idle"]
        N -->|1 or more| P[For Each Payload:]
        P --> Q[Determine Target Listener]
        Q --> R["Append Target Name<br>to Current Path<br>(new thread ID)"]
        R --> S["Inject <from><br>(listener name)"]
        S --> T["Enqueue New Message(s)<br>to Deepened Path(s)<br>(parallel if multi)"]
        T --> U[On Response Bubbling:<br>Pop Last Segment<br>Route to Parent Path]
    end

    C --> DispatcherLoop
    DispatcherLoop --> Response
    Response --> DispatcherLoop
    style Ingress fill:#f0f8ff
    style DispatcherLoop fill:#f0fff0
    style Response fill:#fff0f0
```

```mermaid
flowchart TD
    subgraph MessagePump
    subgraph Init
    start([Start])
    raw[/Optional<br>Raw Bytes/]
    wrapstart["Wrap<br>&ltstart&gt{...}&lt/start&gt"]
    end
    enq1([QUEUE 1])
    rawwaiting{Raw<br>Msg<br>Waiting?}
    waitRaw([Wait])
    subgraph Process
    extract["Extract<br>Tree"]
    split["Split<br>Tree"]
    subgraph foreach [For Each Message]
        getmsg[Get Msg]
        badTo{&ltTo&gt Missing?}
        endnoto([Discard])
        addfrom["Add .from"]
        repair[Repair + C14N]
        validate[Validate]
        invalidMsg{Bad<br>Message?}
        badmsg([Discard])
        more{More?}
    end
    enqueue([QUEUE 2])
    xmlWaiting{XML<br>Waiting?}
    waitXml([Wait])
    subgraph Async
    lookup[Lookup Listener] 
    route[Route]
    wait[await Response]
    wrap["Wrap<br>&ltfrom&gt{...}&lt/from&gt"]
    end
    end
    end
    
    start --> raw --> wrapstart --> enq1 --> rawwaiting 
    rawwaiting --> |NO| waitRaw
    rawwaiting ---> |YES| extract
    extract --> split --> foreach
    getmsg --> badTo
    badTo --> |YES| endnoto
    badTo --> |NO| addfrom --> repair --> validate --> invalidMsg
    invalidMsg ---> |NO| more --> |Yes| getmsg
    invalidMsg --> |YES| badmsg
    more --> |NO| enqueue
    enqueue --> xmlWaiting
    xmlWaiting --> |NO| waitXml
    xmlWaiting ---> |YES| lookup --> route --> wait --> wrap --> enq1
    
```
## Detailed Stages (Per-Message)

1. **Ingress/Enqueue**: Raw bytes → repair → preliminary tree → enqueue to target thread buffer.

2. **Dispatcher Loop**: Single async non-blocking loop selects next message from per-thread queues (breadth-first default for fairness).

3. **Processing**:
   - Full repair + C14N.
   - Envelope validation.
   - Routing decision:
     - **Meta Branch** (`https://xml-pipeline.org/ns/meta/v1` namespace): Handled directly by privileged core handler (no listener lookup or XSD validation needed).
       - Purpose: Introspection and reserved organism primitives.
       - Examples:
         - `request-schema`, `request-example`, `request-prompt`, `list-capabilities` (returns XSD bytes, example XML, prompt fragment, or capability list).
         - Thread primitives like `spawn-thread`, `clear-context`.
       - Privileged: Controlled via YAML `meta` flags (e.g., `allow_schema_requests: "admin"` or "none"). Remote queries optional.
       - Why separate: Faster, safer (no user listener involved), topology privacy preserved.
     - Capability namespace → normal listener route (XSD validation + deserialization).

   - Typed handler call → raw bytes.

4. **Response Handling**:
   - Dummy wrap → extract multi-payloads.
   - Each enqueued as new message(s) in appropriate thread buffer(s).

5. **Egress**: Dequeue → C14N/sign → send.

## Key Properties
- Continuous looping until all thread buffers empty — natural iteration/subthreading without one-shot constraints.
- Multi-payload enqueues enable parallel branches/thoughts.
- Scheduling balances deep dives vs fair exploration.
- Attack-resistant at every step.

XML in → queued → processed → multi-out → re-queued. Loops forever if needed. Safely. Permanently.