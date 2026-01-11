#!/usr/bin/env python3
"""
run_organism.py — Start the organism with TUI console.

Usage:
    python run_organism.py [config.yaml]
    python run_organism.py --simple [config.yaml]  # Use simple console

This boots the organism with a split-screen terminal UI:
- Scrolling output area above
- Status bar separator
- Input area below

Flow:
  1. Bootstrap organism
  2. Start pump in background
  3. Run TUI console
  4. /quit shuts down gracefully
"""

import asyncio
import sys
from pathlib import Path

from agentserver.message_bus import bootstrap
from agentserver.console.console_registry import set_console


async def run_organism(config_path: str = "config/organism.yaml", use_simple: bool = False):
    """Boot organism with TUI console."""

    # Bootstrap the pump
    pump = await bootstrap(config_path)

    if use_simple:
        # Use old SecureConsole for compatibility
        from agentserver.console import SecureConsole
        console = SecureConsole(pump)
        if not await console.authenticate():
            print("Authentication failed.")
            return
        set_console(None)

        pump_task = asyncio.create_task(pump.run())
        try:
            await console.run_command_loop()
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass
            await pump.shutdown()
        print("Goodbye!")
    else:
        # Use new TUI console
        from agentserver.console.tui_console import TUIConsole
        console = TUIConsole(pump)
        set_console(console)  # Register for handlers to find

        # Start pump in background
        pump_task = asyncio.create_task(pump.run())

        try:
            await console.run()
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass
            await pump.shutdown()


def main():
    args = sys.argv[1:]
    use_simple = "--simple" in args
    if use_simple:
        args.remove("--simple")

    config_path = args[0] if args else "config/organism.yaml"

    if not Path(config_path).exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)

    try:
        asyncio.run(run_organism(config_path, use_simple=use_simple))
    except KeyboardInterrupt:
        print("\nInterrupted")


if __name__ == "__main__":
    main()
