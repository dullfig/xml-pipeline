# Secure Console Design — v3.0

**Status:** Design Draft
**Date:** January 2026

## Overview

The console becomes the **sole privileged interface** to the organism. OOB channel is eliminated as a network port — privileged operations are only accessible via local keyboard input.

## Security Model

### Threat Model

| Vector | Risk | Mitigation |
|--------|------|------------|
| Remote attacker | Send privileged commands | No network port — keyboard only |
| Malicious agent | Forge privileged XML | Agents only speak through bus; console hooks directly to handlers |
| Local malware | Keylog password | Out of scope (compromised host = game over) |
| Shoulder surfing | See password | Password not echoed; hash stored, not plaintext |

### Trust Hierarchy

```
┌─────────────────────────────────────────┐
│  Keyboard Input (prompt_toolkit)        │  ← TRUSTED (local human)
│  Password-protected privileged commands │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│  Console Handler                        │
│  /commands → direct privileged hooks    │
│  @messages → message bus (untrusted)    │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│  Message Bus                            │  ← UNTRUSTED (agents, network)
│  All traffic validated, sandboxed       │
└─────────────────────────────────────────┘
```

### Key Principle

**Keyboard = Local = Trusted**

No privileged port. No OOB socket. The only way to issue privileged commands is to be physically present at the keyboard.

## Password Protection

### Password Hash Storage

Password hash stored in `~/.xml-pipeline/console.key` (chmod 600):

```yaml
# console.key
algorithm: argon2id
hash: $argon2id$v=19$m=65536,t=3,p=4$...
created: 2026-01-10T12:00:00Z
```

### Password Workflow

1. **First run:** Console prompts to set password
2. **Startup:** Console prompts for password before accepting any input
3. **Protected commands:** Require password re-entry (see below)

### Password-Protected Commands

| Command | Requires Password | Rationale |
|---------|-------------------|-----------|
| `/restart` | Yes | Disrupts all in-flight operations |
| `/kill <thread>` | Yes | Terminates agent work |
| `/quit` | No | Just exits cleanly |
| `/config` | No | Read-only view |
| `/status` | No | Informational |

## Console Commands

### Informational (No Password)

| Command | Action |
|---------|--------|
| `/help` | Show available commands |
| `/status` | Organism stats (uptime, message count, etc.) |
| `/listeners` | List registered listeners |
| `/threads` | List active threads with age and depth |
| `/buffer <thread-id>` | Inspect context buffer for thread |
| `/config` | View current organism.yaml (read-only) |

### Operational (Password Required)

| Command | Action |
|---------|--------|
| `/restart` | Restart the pipeline (requires password) |
| `/kill <thread-id>` | Terminate a thread (requires password) |
| `/pause` | Pause message processing |
| `/resume` | Resume message processing |

### Session

| Command | Action |
|---------|--------|
| `/quit` | Graceful shutdown |
| `/passwd` | Change console password |

## Configuration Philosophy

### Read-Only at Runtime

For v3.0, the organism.yaml is **read-only while running**:

- `/config` shows the current config (view only)
- To modify: `/quit` → edit yaml → restart
- No hot-reload complexity

### Future Consideration (v4.0+)

Hot-reload is complex:
- What happens to in-flight messages when a listener is removed?
- How to drain a listener before removing?
- Schema changes mid-conversation?

Deferred to future version with careful design.

## Implementation

### Dependencies

```
prompt_toolkit    # Rich terminal input
argon2-cffi       # Password hashing
```

### Console Architecture

```python
class SecureConsole:
    """Privileged console with password protection."""

    def __init__(self, pump: StreamPump, key_path: Path):
        self.pump = pump
        self.key_path = key_path
        self.password_hash: str | None = None
        self.authenticated = False
        self.paused = False

    async def run(self):
        """Main console loop."""
        # Load or create password
        await self._ensure_password()

        # Authenticate
        if not await self._authenticate():
            print("Authentication failed. Exiting.")
            return

        # Main loop with prompt_toolkit
        session = PromptSession(history=FileHistory('~/.xml-pipeline/history'))

        while True:
            try:
                line = await session.prompt_async('> ')
                await self._handle_input(line)
            except EOFError:
                break
            except KeyboardInterrupt:
                continue

    async def _handle_input(self, line: str):
        """Route input to handler."""
        if line.startswith('/'):
            await self._handle_command(line)
        elif line.startswith('@'):
            await self._handle_message(line)
        else:
            print("Use @listener message or /command")

    async def _handle_command(self, line: str):
        """Handle privileged command."""
        cmd, *args = line[1:].split(None, 1)

        if cmd in PROTECTED_COMMANDS:
            if not await self._verify_password():
                print("Password required.")
                return

        handler = getattr(self, f'_cmd_{cmd}', None)
        if handler:
            await handler(args[0] if args else None)
        else:
            print(f"Unknown command: /{cmd}")

    async def _verify_password(self) -> bool:
        """Prompt for password verification."""
        password = await prompt_async('Password: ', is_password=True)
        return argon2.verify(self.password_hash, password)
```

### OOB Channel Removal

The current OOB port in `privileged-msg.xsd` is **removed**. Privileged operations are:

1. Defined as Python methods on `SecureConsole`
2. Invoked directly via keyboard commands
3. Never exposed on any network interface

```python
# OLD (removed):
# oob_server = await start_oob_server(port=8766)

# NEW:
# Privileged ops are just methods on SecureConsole
async def _cmd_restart(self, args: str | None):
    """Restart the pipeline."""
    print("Restarting pipeline...")
    await self.pump.shutdown()
    # Re-bootstrap and run
    self.pump = await bootstrap('config/organism.yaml')
    asyncio.create_task(self.pump.run())
    print("Pipeline restarted.")
```

## UI/UX

### Startup

```
$ python run_organism.py

╔══════════════════════════════════════════╗
║        xml-pipeline console v3.0         ║
╚══════════════════════════════════════════╝

Password: ********

Organism 'hello-world' ready.
5 listeners registered.
Type /help for commands.

>
```

### Example Session

```
> /status
Organism: hello-world
Uptime: 00:05:23
Threads: 3 active
Messages: 47 processed
Buffer: 128 slots across 3 threads

> /listeners
  console          Interactive console
  console-router   Routes console input
  greeter          [agent] Greeting agent
  shouter          Shouts responses
  response-handler Forwards to console

> @greeter Hello world
[sending to greeter]
[shouter] HELLO WORLD!

> /threads
  a1b2c3d4...  age=00:02:15  depth=3  greeter→shouter→response-handler
  e5f6g7h8...  age=00:00:45  depth=1  greeter

> /kill a1b2c3d4
Password: ********
Thread a1b2c3d4 terminated.

> /restart
Password: ********
Restarting pipeline...
Pipeline restarted.

> /quit
Goodbye!
```

## Security Checklist

- [ ] Password hash file has mode 600
- [ ] Password never stored in plaintext
- [ ] Password never logged
- [ ] Password not echoed during input
- [ ] No network port for privileged operations
- [ ] Protected commands require password re-entry
- [ ] Argon2id for password hashing (memory-hard)

## Migration from v2.x

1. Remove OOB port configuration from organism.yaml
2. Remove `privileged-msg.xsd` network handling
3. First run prompts for password setup
4. Existing privileged operations become console commands

## Attach/Detach Model

The console is a proper handler in the message flow. It can attach and detach without stopping the organism.

### Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Startup                                                     │
│  Password: ********                                          │
│  /attach issued automatically                                │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Attached State                                              │
│  Console handler awaits keyboard input                       │
│  > @greeter hello                                            │
│  > /status                                                   │
└─────────────────────────────────────────────────────────────┘
                              ↓ (idle timeout, e.g. 30 min)
┌─────────────────────────────────────────────────────────────┐
│  Detached State                                              │
│  Console handler returns None → await closed                 │
│  Organism keeps running headless                             │
│  Output queued to ring buffer (last N messages)              │
└─────────────────────────────────────────────────────────────┘
                              ↓ (/attach + password)
┌─────────────────────────────────────────────────────────────┐
│  Re-attached                                                 │
│  Queued output displayed                                     │
│  Console resumes awaiting input                              │
└─────────────────────────────────────────────────────────────┘
```

### Implementation

```python
async def handle_console_prompt(payload: ConsolePrompt, metadata: HandlerMetadata):
    """Console handler with timeout support."""

    # Display output
    if payload.output:
        print_colored(payload.output, source=payload.source)

    # Wait for input with timeout
    try:
        line = await asyncio.wait_for(read_input(), timeout=IDLE_TIMEOUT)
    except asyncio.TimeoutError:
        print_colored("Idle timeout. Detaching console.", Colors.YELLOW)
        return None  # ← Detach: closes the await, organism continues

    # Process input...
    return HandlerResponse(...)
```

### Detached Behavior

When console is detached:

| Concern | Behavior |
|---------|----------|
| Messages to console | Queued in ring buffer (last 100) |
| Organism operation | Continues normally |
| Logging | All output logged to file |
| Re-attach | `/attach` displays queued messages |

### Commands

| Command | Action |
|---------|--------|
| `/attach` | Attach console (requires password if detached) |
| `/detach` | Manually detach (organism keeps running) |
| `/timeout <minutes>` | Set idle timeout (0 = disabled) |

## Open Questions

1. **Audit log?** Log all privileged commands to file?
2. **Multi-user?** Multiple passwords with different privilege levels?
3. **Remote console?** SSH tunnel? (deferred — complexity)
4. **Detached notifications?** Beep/alert when important messages arrive?

---

*This design prioritizes simplicity and security. The keyboard-only model eliminates an entire class of remote attacks while providing a rich local interface for operators.*
