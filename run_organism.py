#!/usr/bin/env python3
"""
run_organism.py — Start the organism.

Usage:
    python run_organism.py [config.yaml]

This boots the organism and runs the message pump.
The console is a regular handler in the message flow:

  boot -> system.boot -> console (await input) -> console-router -> ... -> console

The pump continues until the console returns None (EOF or /quit).
"""

import asyncio
import sys
from pathlib import Path

from agentserver.message_bus import bootstrap


async def run_organism(config_path: str = "config/organism.yaml"):
    """Boot organism and run the message pump."""

    # Bootstrap the pump (registers listeners, injects boot message)
    pump = await bootstrap(config_path)

    # Set pump reference for console introspection commands
    from handlers.console import set_pump_ref
    set_pump_ref(pump)

    # Run the pump - it will process boot -> console -> ... flow
    # The pump runs until shutdown is called
    try:
        await pump.run()
    except asyncio.CancelledError:
        pass
    finally:
        await pump.shutdown()

    print("Goodbye!")


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/organism.yaml"

    if not Path(config_path).exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)

    try:
        asyncio.run(run_organism(config_path))
    except KeyboardInterrupt:
        print("\nInterrupted")


if __name__ == "__main__":
    main()
