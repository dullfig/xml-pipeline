# Semantic Dispatch & Structured Context

**Status:** Back-of-napkin / Vision document
**Date:** 2026-02-17

This document captures early design ideas for two related concepts:
replacing forced XML tool-call output with semantic routing, and
introducing structured primitives into LLM agent context.

---

## 1. Semantic Routing for Tool Dispatch

### Problem

Currently, LLM agents must produce well-formed XML to call tools:

```xml
<researcher.shell-request>
  <command>find . -name "*.yaml"</command>
</researcher.shell-request>
```

This is brittle. The LLM must remember schema structure, balance tags, and
match exact root tag names. Repair cycles burn tokens when it gets it wrong.

### Idea

Let the LLM speak naturally. Use embedding-based semantic routing
(e.g., [aurelio-labs/semantic-router](https://github.com/aurelio-labs/semantic-router))
to detect tool-call intent and route to the correct listener.

Instead of structured XML, the LLM just says:

> "Now I need to find all the YAML config files in the project."

The semantic router matches this to the `shell` listener, the system extracts
parameters and builds the envelope programmatically. The rest of the pipeline
(peer enforcement, thread scoping, envelope injection) stays untouched.

### `semantic_guide` — Arbitrarily Long Tool Descriptions

A new YAML field on listeners. Unlike `description` (short, may appear in LLM
prompts), the `semantic_guide` is only ever seen by the embedding model. It can
be as long as needed — cost is negligible (one-time embed).

```yaml
listeners:
  - name: shell
    payload_class: tools.shell.ShellRequest
    handler: tools.shell.handle_shell
    description: "Execute shell commands"
    semantic_guide: |
      Use this when you need to interact with the operating system.

      Finding files: "find all python files", "locate the config",
        "where is the database schema"
      Running scripts: "execute the build", "run the tests",
        "start the server"
      Inspecting state: "check disk space", "list running processes",
        "show environment variables", "what's on port 8080"
      Text processing: "grep for errors in the logs",
        "count lines in the file", "search for TODO comments"

      Do NOT use for:
        - Math calculations (use calculator)
        - Web searches (use search)
        - Key-value storage (use keyvalue)

      Negative examples (push decision boundary away):
        "what is 2+2" → NOT shell
        "search the web for recent news" → NOT shell
        "store this value for later" → NOT shell
```

Key properties:

- **Negative examples** are as valuable as positive ones — they sharpen the
  decision boundary between similar tools.
- **No token budget pressure** — a 2000-word guide costs fractions of a cent
  to embed, versus eating context window on every LLM call.
- **Domain expert authored** — the person who builds the tool writes the guide
  in plain English, no XML schema knowledge required.
- **Hot-reload friendly** — when a listener is added/updated via OOB, re-encode
  its guide and update the index. Routes stay in sync with live topology.

### Auto-Generation from Listener Registry

Routes can be auto-generated from existing listener registrations:

- `name` → route name
- `description` + `semantic_guide` → utterance corpus
- `payload_class` fields → function schema for parameter extraction

No manual route definition needed. The pump already has everything.

---

## 2. Scratch Pad — Ephemeral Sub-Context for Tool Calls

### Problem

Tool call negotiation (parameter clarification, error retries, raw output)
pollutes the main conversation context. A shell command that returns 200 lines
of stdout lives in context forever, even if the LLM only needed one line.

### Idea

When a tool call is detected, a **scratch pad** opens — a temporary, isolated
context for the tool interaction. The main context is suspended.

```
Main context: [A, B, C, "I should check what's running on port 8080"]
                                    │
                          semantic router fires
                                    │
                    ┌───────────────────────────────┐
                    │         SCRATCH PAD            │
                    │                                │
                    │  system: matched → shell       │
                    │  system: extracted command:     │
                    │    lsof -i :8080               │
                    │  [tool executes]               │
                    │  stdout: (47 lines)            │
                    │  system: node on 8080, pid 924 │
                    └───────────────────────────────┘
                                    │
                            scratch pad collapses
                                    │
Main context: [A, B, C, "I should check what's running on port 8080",
               tool_result: "node process (pid 924) listening on :8080"]
```

Properties:

- **Context stays lean** — negotiation, raw output, retries are all ephemeral.
- **Retries are free** — if the first command fails, the scratch pad retries
  with a corrected command. The main context never sees the fumble.
- **Clarification without pollution** — system can ask "did you mean recursive?"
  in the scratch pad. That Q&A vanishes after resolution.
- **Maps to existing architecture** — the scratch pad is a short-lived child
  thread. Thread scoping, opaque UUIDs, and context buffers already exist.

### What Survives the Collapse

The `payload_class` of the response defines the shape of what survives.
The tool author decides what's worth remembering:

```python
@xmlify
@dataclass
class ShellResult:
    exit_code: int
    summary: str      # survives into main context
    # stdout, stderr  # lived only in the scratch pad
```

---

## 3. Stack Frames — Self-Calls with Context Inheritance

### Problem

Tool calls need isolation (the calculator doesn't need your life story).
But self-calls — when an agent calls itself to think more deeply — need the
opposite. The sub-task needs full context to reason properly.

### Idea

Two routing modes, determined automatically by the semantic router:

**Tool call → fork (isolation)**
```
Main context ──┬──────────────────────
               │
         [scratch pad]  (sees only its own stuff)
               │
Main context ──┴── + result ──────────
```

**Self-call → stack (inheritance)**
```
Main context ──┬──────────────────────
               │
         [self frame]   (sees EVERYTHING + its own sub-task)
               │
Main context ──┴── + summary ─────────
```

The self-call frame inherits the full parent context. The agent has all the
information it needs to reason about the sub-task. When it returns, only the
summary survives — the 15 messages of intermediate reasoning are pruned.

```
result = self.call(task="figure out the deployment order",
                   inherits=full_context)
# main context sees only: "deploy order: DB → API → frontend"
# the reasoning chain? gone
```

This makes chain-of-thought a **first-class primitive with automatic garbage
collection**. Currently CoT either lives forever in context (expensive) or
gets dropped entirely (lossy). Stack frames let the agent think deeply and
only keep the conclusion.

The `max_tokens_per_thread` budget goes further — sub-frames burn tokens
while they exist but release them when they collapse.

---

## 4. Forms — Structured Input as a Context Primitive

### Problem

Tool schemas are currently presented as text descriptions or XML examples.
The LLM has to remember structure, field names, types, and defaults. This is
error-prone and wastes context on formatting instructions.

### Idea

When a tool call is detected, the scratch pad presents a **form** — a
fill-in-the-blank template generated from the `payload_class`:

```
┌─────────────────────────────────┐
│ SHELL COMMAND                   │
│                                 │
│ command:  ___________________   │
│ workdir:  ___________________ ? │
│ timeout:  [30s]               ? │
│                                 │
│ ? = optional, default shown     │
└─────────────────────────────────┘
```

The form is auto-generated from the dataclass:

```python
@xmlify
@dataclass
class ShellRequest:
    command: str              # required → blank line
    workdir: str = "."        # optional → default shown
    timeout: int = 30         # optional → default shown
```

Properties:

- **LLMs excel at fill-in-the-blank** — dramatically less error-prone than
  free-form structure generation.
- **Validation is immediate** — LLM writes `timeout: "fast"` → form rejects
  it, says "timeout must be an integer" right in the scratch pad. Retry is
  cheap because it's ephemeral.
- **Forms compose** — complex tools can be multi-step. "What kind of search?"
  → [web / files / code] → second form with context-appropriate fields.
  Guide the LLM through a decision tree instead of dumping the whole tree.

---

## 5. Living Checklists — Plans as Context Primitives

### Problem

When an agent executes a multi-step plan, it loses track of progress. It has
to re-read the entire conversation to figure out what's done and what's next.
If context gets compressed, progress state can be lost entirely.

### Idea

Plans are rendered as persistent checklists in the context. The system manages
checkmarks — when a self-call frame collapses with a success summary, the
corresponding item is checked automatically.

```
╔══════════════════════════════════════╗
║  PLAN: Migrate database to Postgres  ║
║                                      ║
║  [✓] Audit current SQLite queries    ║
║  [✓] Write Postgres schema           ║
║  [►] Create migration script     ◄── you are here
║  [ ] Update connection config        ║
║  [ ] Run integration tests           ║
╚══════════════════════════════════════╝
```

The LLM always sees its progress without re-reading 50 messages. The checklist
is a **structural primitive**, not buried text — it survives context compression.

The LLM doesn't need to say "I'm done with step 3." The system knows because
the frame returned.

---

## 6. Structured Context — The Unifying Model

Current LLM systems have one context primitive: **messages**. Everything is
a flat list of messages. Tool schemas, progress tracking, intermediate
reasoning — all crammed into the same flat log.

This vision adds three more primitives:

| Primitive | Lifetime | Visibility | Purpose |
|-----------|----------|------------|---------|
| **Message** | permanent | main context | conversation history |
| **Checklist** | plan duration | always visible, top of context | progress and orientation |
| **Form** | until submitted | scratch pad | structured tool input |
| **Frame** | until return | stacked (self) or forked (tool) | sub-task execution |

Together they provide:

- **Orientation** — the agent always knows where it is (checklist)
- **Structure** — tool input is guided, not free-form (form)
- **Hygiene** — intermediate work is pruned automatically (frame)
- **History** — what actually matters is preserved (message)

### Context Layout at Any Point in Time

```
╔═ CHECKLIST ══════════════════════════╗
║  [✓] Step 1    [►] Step 2    [ ] ... ║
╚══════════════════════════════════════╝

┌─ MESSAGES ───────────────────────────┐
│ user: "Migrate the database"         │
│ agent: "I'll start by auditing..."   │
│ [frame₁ summary]: "Found 12 queries" │
│ agent: "Now writing the schema..."   │
│ [frame₂ summary]: "Schema ready"    │
│ agent: "Let me create the migration" │
└──────────────────────────────────────┘

┌─ ACTIVE FRAME (self-call) ───────────┐
│ inherits: ▲ full context above       │
│ task: "Create migration script"      │
│ [working...]                         │
│                                      │
│ ┌─ FORM (shell) ──────────────────┐  │
│ │ command: ___________________    │  │
│ │ workdir: [.]                    │  │
│ └─────────────────────────────────┘  │
└──────────────────────────────────────┘
```

---

## 7. Dual Rendering — Presentation Layer over XML

### Problem

The pretty tables, forms, and checklists described above are designed for
human readability. But the LLM doesn't need (or benefit from) box-drawing
characters and visual layout. Conversely, humans shouldn't have to read
serialized XML. Current systems pick one format and force both audiences
to use it.

### Idea

XML is the canonical representation for all context primitives — consistent
with xml-pipeline's existing philosophy. A **presentation layer** renders
the same XML differently depending on the viewer:

```
              XML (source of truth)
               /              \
         Human view         LLM view
        ┌──────────┐     <checklist>
        │ [✓] Step1│       <item status="done">Step1</item>
        │ [►] Step2│       <item status="active">Step2</item>
        │ [ ] Step3│       <item status="pending">Step3</item>
        └──────────┘     </checklist>
```

A form, as the human sees it:

```
┌─────────────────────────────────┐
│ SHELL COMMAND                   │
│                                 │
│ command:  ___________________   │
│ workdir:  ___________________ ? │
│ timeout:  [30s]               ? │
└─────────────────────────────────┘
```

The same form, as the LLM sees it:

```xml
<form tool="shell">
  <field name="command" type="str" required="true"/>
  <field name="workdir" type="str" required="false" default="."/>
  <field name="timeout" type="int" required="false" default="30"/>
</form>
```

The LLM fills it in by producing:

```xml
<form tool="shell">
  <field name="command">lsof -i :8080</field>
</form>
```

No pretty-printing, no box-drawing. Just structured data in, structured data
out. The XML is trivially parseable, validatable against the form schema, and
round-trips cleanly — which is what the system already does for everything else.

### Why This Matters

- **Each audience gets the optimal format.** Humans get visual affordances
  (checkboxes, tables, borders). LLMs get clean, parseable structure.
- **One source of truth.** The XML is canonical. The human view is a
  rendering pass, not a separate artifact. No sync issues.
- **Consistent with the project.** XML is already the sovereign wire format.
  Context primitives being XML means they flow through the same pipeline —
  validation, C14N, signing — as everything else.
- **The presentation layer is swappable.** Terminal UI, web dashboard, IDE
  plugin — all just different renderers over the same XML primitives.
- **LLMs don't waste tokens on decoration.** No box-drawing characters, no
  alignment spaces, no visual chrome. Just semantic content.

### Applied to All Primitives

| Primitive | Human rendering | LLM serialization |
|-----------|----------------|-------------------|
| Checklist | `[✓] [►] [ ]` visual list | `<checklist><item status="done">` |
| Form | Bordered box with blanks | `<form><field name="" type="">` |
| Frame | Indented sub-section | `<frame type="self" inherits="true">` |
| Message | Chat bubble / formatted text | `<message from="" to="">` (already exists) |
| Tool result | Formatted summary card | `<result tool="" exit-code="">` |

The pretty-print examples throughout this document are the *human* rendering.
The wire format is always XML.

---

## Open Questions

- **Parameter extraction:** When a form is filled, is validation enough or
  does an LLM call sometimes help for ambiguous inputs?
- **Compression interaction:** How do structural primitives interact with
  automatic context compression? Are they exempt? Re-injected after?
- **Semantic guide authoring:** Best practices for writing effective guides.
  How many examples are enough? How important are negative examples?
- **Threshold tuning:** Per-tool semantic routing thresholds. How to set
  them without labeled data? Auto-calibration from the guide?
- **Multi-tool utterances:** "Search the web and then summarize it" — does
  the router match one tool or chain them?
- **Transition strategy:** How to incrementally adopt alongside the existing
  XML pipeline without breaking current guarantees.

---

## References

- [aurelio-labs/semantic-router](https://github.com/aurelio-labs/semantic-router) —
  Embedding-based decision layer for LLM applications
- [xml-pipeline handler contract](handler-contract-v2.1.md) — Current handler spec
- [xml-pipeline message pump](message-pump-v2.1.md) — Current pipeline architecture
