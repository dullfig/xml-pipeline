#!/usr/bin/env python3
"""
run_organism.py — Start the organism with secure console.

Usage:
    python run_organism.py [config.yaml]

This boots the organism with a password-protected console.
The secure console handles privileged operations via local keyboard only.

Flow:
  1. Password authentication
  2. Pump starts processing messagest
  3. Console handles commands and @messages
  4. /quit shuts down gracefully
"""

import asyncio
import sys
from pathlib import Path

from agentserver.message_bus import bootstrap
from agentserver.console import SecureConsole


async def run_organism(config_path: str = "config/organism.yaml"):
    """Boot organism with secure console."""

    # Bootstrap the pump (registers listeners, but DON'T start yet)
    pump = await bootstrap(config_path)

    # Create secure console and authenticate FIRST
    console = SecureConsole(pump)

    # Authenticate before starting pump
    if not await console.authenticate():
        print("Authentication failed.")
        return

    # Now start the pump in background
    pump_task = asyncio.create_task(pump.run())

    try:
        # Run console command loop (already authenticated)
        await console.run_command_loop()
    finally:
        # Ensure pump is shut down
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass
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
