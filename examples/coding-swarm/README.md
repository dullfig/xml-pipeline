# Coding Swarm

Multi-agent organism that collaborates to write AssemblyScript WASM tools.

## Architecture

Five LLM-powered agents and three scoped tool listeners wired through xml-pipeline's message bus:

| Listener | Type | Purpose |
|----------|------|---------|
| coordinator | FSM (no LLM) | Orchestrates the workflow via state machine |
| architect | LLM agent | Designs WIT interface definitions |
| coder | LLM agent | Writes AssemblyScript implementations |
| tester | LLM agent | Writes pytest test cases |
| reviewer | LLM agent | Reviews code quality (approve/reject) |
| workspace-read | Tool | Reads files from sandboxed workspace |
| workspace-write | Tool | Writes files to sandboxed workspace |
| build-run | Tool | Executes whitelisted commands |

## Workflow

```
User ──> Coordinator ──> Architect ──> Coordinator ──> workspace-write ──> Coordinator
              |                                              ^
              +──> Coder ──> Coordinator ──> workspace-write +
              |
              +──> Tester ──> Coordinator (retry if fail, max 3)
              |
              +──> Reviewer ──> Coordinator (retry if reject, max 2)
```

## Running

```bash
# Requires an LLM API key
export XAI_API_KEY=your-key-here

# Run the organism
xml-pipeline run examples/coding-swarm/organism.yaml
```

## Testing

Deterministic tests (no LLM required):

```bash
pytest tests/test_coding_swarm.py -v
```
