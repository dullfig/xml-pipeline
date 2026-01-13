# Native Tools

Tools available to agents for interacting with the outside world. Each tool is sandboxed and permission-controlled.

## Tool Interface

All tools follow a common pattern:

```python
@tool
async def tool_name(param1: str, param2: int = default) -> ToolResult:
    """Tool description for LLM."""
    # Implementation
    return ToolResult(success=True, data=result)
```

Agents invoke tools via the platform:

```python
result = await platform.tool("calculate", expression="sqrt(16) + pi")
```

---

## Core Tools

### calculate

Evaluate mathematical expressions safely using Python syntax.

**Implementation:** `simpleeval` library

```
Tool: calculate
Input: expression (string) - A math expression using Python syntax

Supported:
- Basic ops: + - * / // % **
- Comparisons: < > <= >= == !=
- Functions: abs, round, min, max, sqrt, sin, cos, tan, log, log10
- Constants: pi, e
- Parentheses for grouping

Examples:
- "2 + 2" → 4
- "(10 + 5) * 3" → 45
- "sqrt(16) + pi" → 7.141592...
- "max(10, 20, 15)" → 20
- "round(3.14159, 2)" → 3.14
- "2 ** 10" → 1024
```

**Implementation:**

```python
from simpleeval import simple_eval
import math

MATH_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
}

MATH_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
}

@tool
async def calculate(expression: str) -> ToolResult:
    """Evaluate a mathematical expression using Python syntax."""
    try:
        result = simple_eval(
            expression,
            functions=MATH_FUNCTIONS,
            names=MATH_CONSTANTS,
        )
        return ToolResult(success=True, data=result)
    except Exception as e:
        return ToolResult(success=False, error=str(e))
```

---

### fetch_url

Retrieve content from a URL.

```
Tool: fetch_url
Inputs:
  - url (string) - The URL to fetch
  - method (string, optional) - GET, POST, PUT, DELETE (default: GET)
  - headers (object, optional) - HTTP headers
  - body (string, optional) - Request body for POST/PUT

Returns:
  - status_code (int)
  - headers (object)
  - body (string)

Examples:
- fetch_url("https://api.example.com/data")
- fetch_url("https://api.example.com/submit", method="POST", body='{"key": "value"}')
```

**Security:**
- URL allowlist/blocklist configurable
- Timeout enforced
- Response size limit
- No file:// or internal IPs by default

---

### read_file

Read contents of a file.

```
Tool: read_file
Inputs:
  - path (string) - Path to file
  - encoding (string, optional) - Text encoding (default: utf-8)
  - binary (bool, optional) - Return base64 if true (default: false)

Returns:
  - content (string) - File contents

Examples:
- read_file("/data/config.yaml")
- read_file("/data/image.png", binary=true)
```

**Security:**
- Chroot to allowed directories
- No path traversal (../)
- Size limit enforced

---

### write_file

Write content to a file.

```
Tool: write_file
Inputs:
  - path (string) - Path to file
  - content (string) - Content to write
  - mode (string, optional) - "overwrite" or "append" (default: overwrite)
  - encoding (string, optional) - Text encoding (default: utf-8)

Returns:
  - bytes_written (int)

Examples:
- write_file("/output/result.txt", "Hello, world!")
- write_file("/logs/agent.log", "New entry\n", mode="append")
```

**Security:**
- Chroot to allowed directories
- No path traversal
- Max file size enforced

---

### list_dir

List directory contents.

```
Tool: list_dir
Inputs:
  - path (string) - Directory path
  - pattern (string, optional) - Glob pattern filter (default: *)

Returns:
  - entries (array of {name, type, size, modified})

Examples:
- list_dir("/data")
- list_dir("/data", pattern="*.xml")
```

---

### run_command

Execute a shell command (sandboxed).

```
Tool: run_command
Inputs:
  - command (string) - Command to execute
  - timeout (int, optional) - Timeout in seconds (default: 30)
  - cwd (string, optional) - Working directory

Returns:
  - exit_code (int)
  - stdout (string)
  - stderr (string)

Examples:
- run_command("ls -la")
- run_command("python script.py --input data.csv", timeout=60)
```

**Security:**
- Command allowlist (or blocklist dangerous commands)
- No shell expansion by default
- Resource limits (CPU, memory)
- Chroot to safe directory
- Timeout enforced

---

### web_search

Search the web.

```
Tool: web_search
Inputs:
  - query (string) - Search query
  - num_results (int, optional) - Number of results (default: 5, max: 20)

Returns:
  - results (array of {title, url, snippet})

Examples:
- web_search("python xml parsing best practices")
- web_search("latest AI news", num_results=10)
```

**Implementation options:**
- SerpAPI
- Google Custom Search
- Bing Search API
- DuckDuckGo (scraping)

---

### key_value_store

Persistent key-value storage for agent state.

```
Tool: key_value_get
Inputs:
  - key (string) - Key to retrieve
  - namespace (string, optional) - Namespace for isolation (default: agent name)

Returns:
  - value (any) - Stored value, or null if not found

---

Tool: key_value_set
Inputs:
  - key (string) - Key to store
  - value (any) - Value to store (JSON-serializable)
  - namespace (string, optional) - Namespace for isolation
  - ttl (int, optional) - Time-to-live in seconds

Returns:
  - success (bool)

---

Tool: key_value_delete
Inputs:
  - key (string) - Key to delete
  - namespace (string, optional)

Returns:
  - deleted (bool)

Examples:
- key_value_set("user_preferences", {"theme": "dark"})
- key_value_get("user_preferences") → {"theme": "dark"}
```

**Implementation:** Redis, SQLite, or in-memory with persistence

---

### send_email

Send an email notification.

```
Tool: send_email
Inputs:
  - to (string or array) - Recipient(s)
  - subject (string) - Email subject
  - body (string) - Email body (plain text or HTML)
  - html (bool, optional) - Treat body as HTML (default: false)

Returns:
  - message_id (string)

Examples:
- send_email("user@example.com", "Report Ready", "Your daily report is attached.")
```

**Security:**
- Recipient allowlist
- Rate limiting
- Template restrictions

---

### webhook

Call a webhook URL.

```
Tool: webhook
Inputs:
  - url (string) - Webhook URL
  - payload (object) - JSON payload
  - method (string, optional) - POST or PUT (default: POST)

Returns:
  - status_code (int)
  - response (string)

Examples:
- webhook("https://hooks.slack.com/xxx", {"text": "Task completed!"})
```

---

## Librarian Tools (exist-db)

XML-native database integration for document storage and XQuery retrieval.

### librarian_store

Store an XML document in exist-db.

```
Tool: librarian_store
Inputs:
  - collection (string) - Target collection path
  - document_name (string) - Document filename
  - content (string) - XML content

Returns:
  - path (string) - Full path to stored document

Examples:
- librarian_store("/db/agents/greeter", "conversation-001.xml", "<thread>...</thread>")
```

---

### librarian_get

Retrieve a document by path.

```
Tool: librarian_get
Inputs:
  - path (string) - Full document path

Returns:
  - content (string) - XML content

Examples:
- librarian_get("/db/agents/greeter/conversation-001.xml")
```

---

### librarian_query

Execute an XQuery against the database.

```
Tool: librarian_query
Inputs:
  - query (string) - XQuery expression
  - collection (string, optional) - Limit to collection
  - variables (object, optional) - External variables to bind

Returns:
  - results (array of string) - Matching XML fragments

Examples:
- librarian_query('//message[@from="greeter"]')
- librarian_query('for $m in //message where $m/@timestamp > $since return $m',
                  variables={"since": "2024-01-15T00:00:00Z"})
```

---

### librarian_search

Full-text search across documents.

```
Tool: librarian_search
Inputs:
  - query (string) - Search terms
  - collection (string, optional) - Limit to collection
  - num_results (int, optional) - Max results (default: 10)

Returns:
  - results (array of {path, score, snippet})

Examples:
- librarian_search("error handling", collection="/db/agents")
```

---

## Tool Permissions

Tools are gated by agent permissions in organism.yaml:

```yaml
listeners:
  - name: researcher
    tools:
      - web_search
      - fetch_url
      - read_file
      - calculate
    tool_config:
      fetch_url:
        allowed_domains: ["api.example.com", "*.github.com"]
      read_file:
        allowed_paths: ["/data/research/**"]

  - name: writer
    tools:
      - write_file
      - send_email
    tool_config:
      write_file:
        allowed_paths: ["/output/**"]
      send_email:
        allowed_recipients: ["team@example.com"]
```

---

## Implementation Checklist

- [ ] Tool base class and registry
- [ ] calculate (simpleeval)
- [ ] fetch_url (aiohttp)
- [ ] read_file / write_file
- [ ] list_dir
- [ ] run_command (asyncio.subprocess)
- [ ] web_search (provider TBD)
- [ ] key_value_store (Redis/SQLite)
- [ ] send_email (SMTP/SendGrid)
- [ ] webhook
- [ ] librarian_* (exist-db REST API)
- [ ] Permission enforcement layer
- [ ] Rate limiting per tool
- [ ] Audit logging for tool calls
