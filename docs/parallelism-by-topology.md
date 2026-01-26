# Parallelism by Topology

**Status:** Architectural Concept  
**Author:** Dan & Donna  
**Date:** 2026-01-26

## Overview

In OpenBlox, **parallelism is a wiring decision, not a configuration option**.

Unlike traditional workflow tools where you toggle "parallel execution" checkboxes or set concurrency limits in config files, OpenBlox uses the flow topology itself to determine whether work is processed sequentially or in parallel.

The key insight: **a buffer node acts as a parallelism primitive**.

## The Problem with Traditional Approaches

Most workflow tools treat parallelism as an afterthought:

```yaml
# n8n / Zapier style
node:
  type: process_video
  concurrency: 3  # Magic number, what if you want 1 sometimes and 10 others?
  batch_size: 5   # More config to maintain
```

This leads to:
- Config sprawl
- Hidden behavior (why did this run in parallel?)
- Hard-to-debug race conditions
- One-size-fits-all concurrency that doesn't adapt to workload

## The OpenBlox Way

Parallelism emerges from how you wire your flow.

### Sequential Execution (Direct Wiring)

```
┌──────────┐      ┌──────────┐      ┌──────────┐
│  Trigger │─────▶│ Process  │─────▶│  Output  │
│  (files) │      │  Video   │      │          │
└──────────┘      └──────────┘      └──────────┘
```

When files are dropped:
1. Trigger fires for File 1 → Process Video receives it
2. Trigger fires for File 2 → Process Video is busy, message queues
3. Trigger fires for File 3 → Also queues
4. Files process **one at a time**, in order

**Use when:** Order matters, resources are limited, or downstream systems can't handle concurrency.

### Parallel Execution (Buffer Wiring)

```
┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
│  Trigger │─────▶│  Buffer  │─────▶│ Process  │─────▶│  Output  │
│  (files) │      │          │      │  Video   │      │          │
└──────────┘      └──────────┘      └──────────┘      └──────────┘
```

When files are dropped:
1. Trigger fires for File 1 → Buffer spawns **Thread A**, forwards to Process Video
2. Trigger fires for File 2 → Buffer spawns **Thread B**, forwards to Process Video
3. Trigger fires for File 3 → Buffer spawns **Thread C**, forwards to Process Video
4. Three independent threads now executing in parallel!

**Use when:** Tasks are independent, you want maximum throughput, or tasks have variable duration.

## Why This Works

### Thread Isolation

Each thread gets:
- **Unique UUID** — Opaque identifier, can't be guessed or forged
- **Independent call chain** — `trigger.buffer.process` per thread
- **Isolated context** — Thread A can't see Thread B's state
- **Separate lifecycle** — Thread A completing doesn't affect Thread B

### Multiprocess Nodes

Every node runs in its own process. When multiple threads arrive:
- Node spawns worker processes as needed
- Each worker handles one thread
- WASM sandboxing ensures isolation
- No shared state, no race conditions

### Natural Load Balancing

The buffer doesn't just fan-out — it **decouples** the trigger from processing:
- Fast tasks (2-minute shorts) complete quickly
- Slow tasks (2-hour features) chug along
- No artificial batching or waiting
- System naturally adapts to workload

## Real-World Example: Video Processing Pipeline

Imagine a content pipeline that receives video requirements:

```
Requirements folder watched by trigger:
├── short_ad_1.json      (30 sec video, ~2 min to process)
├── short_ad_2.json      (30 sec video, ~2 min to process)  
└── feature_film.json    (2 hour video, ~4 hours to process)
```

### Without Buffer (Sequential)

```
Timeline:
├─ 0:00  Start short_ad_1
├─ 0:02  Finish short_ad_1, start short_ad_2
├─ 0:04  Finish short_ad_2, start feature_film
└─ 4:04  Finish feature_film

Total time: 4 hours 4 minutes
Shorts delivered: After 2-4 minutes
```

### With Buffer (Parallel)

```
Timeline:
├─ 0:00  Start short_ad_1 (Thread A)
├─ 0:00  Start short_ad_2 (Thread B)
├─ 0:00  Start feature_film (Thread C)
├─ 0:02  Finish short_ad_1 ✓
├─ 0:02  Finish short_ad_2 ✓
└─ 4:00  Finish feature_film ✓

Total time: 4 hours (wall clock, same as longest task)
Shorts delivered: After 2 minutes!
```

The shorts are ready in **2 minutes** instead of waiting behind the feature film.

## Advanced Patterns

### Controlled Parallelism

Want parallelism but with limits? Use a **throttled buffer**:

```
┌──────────┐      ┌──────────────┐      ┌──────────┐
│  Trigger │─────▶│   Buffer     │─────▶│ Process  │
│          │      │ (max_threads │      │          │
│          │      │    = 3)      │      │          │
└──────────┘      └──────────────┘      └──────────┘
```

Buffer spawns at most 3 threads. Additional items queue until a slot opens.

### Fan-Out / Fan-In

Process items in parallel, then aggregate results:

```
                       ┌──────────┐
                   ┌──▶│ Worker A │──┐
┌────────┐    ┌────┴─┐ └──────────┘  │  ┌───────────┐    ┌────────┐
│Trigger │───▶│Buffer│               ├─▶│ Collector │───▶│ Output │
└────────┘    └────┬─┘ ┌──────────┐  │  └───────────┘    └────────┘
                   └──▶│ Worker B │──┘
                       └──────────┘
```

Collector waits for all threads in its context to complete, then aggregates.

### Conditional Parallelism

Use a **router** before the buffer to decide:

```
┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
│  Trigger │─────▶│  Router  │─────▶│  Buffer  │─────▶│ Process  │
│          │      │ (if big  │      │          │      │          │
│          │      │  file)   │      └──────────┘      └──────────┘
│          │      │          │                              
│          │      │ (else)   │─────────────────────────────▶│
└──────────┘      └──────────┘                              
```

Small files go direct (sequential among themselves), large files get buffered (parallel).

## Implementation Notes

### Buffer Node Contract

A buffer node:
1. Accepts incoming messages
2. For each message, generates a **new thread UUID**
3. Forwards message with new thread to downstream node
4. Does NOT wait for downstream completion (fire-and-forget)

### Thread Registry Behavior

When buffer spawns a new thread:
```
Incoming thread: trigger.buffer (uuid-1)
                      │
                      ▼
Buffer creates:  trigger.buffer.process (uuid-2)  ─▶ Thread A
                 trigger.buffer.process (uuid-3)  ─▶ Thread B  
                 trigger.buffer.process (uuid-4)  ─▶ Thread C
```

Each UUID is independent. Responses from Process don't need to reconverge unless explicitly routed to a collector.

### No Hidden Magic

The behavior is **entirely determined by the visual flow**:
- See a direct wire? Sequential.
- See a buffer? Parallel.
- No config files to check.
- No runtime surprises.

## Comparison with Other Tools

| Feature | n8n/Zapier | Temporal | OpenBlox |
|---------|------------|----------|----------|
| Parallelism control | Config flags | Code annotations | **Topology** |
| Visibility | Hidden in settings | Hidden in code | **Visual in canvas** |
| Flexibility | Fixed at deploy | Fixed at deploy | **Changeable by rewiring** |
| Learning curve | Read docs | Read code | **Look at flow** |

## Summary

> "If you want sequential, wire direct. If you want parallel, add a buffer."

This single principle replaces pages of concurrency documentation. Users learn it once, apply it everywhere, and can see their concurrency decisions directly in the flow canvas.

---

*Parallelism should be obvious, not hidden.*
