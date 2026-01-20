#!/usr/bin/env python3
"""
Run the console example.

Usage:
    python -m examples.console [config.yaml]

If no config is specified, uses the bundled organism.yaml.
"""

import asyncio
import sys
from pathlib import Path


async def main(config_path: str) -> None:
    """Boot organism and run console."""
    from xml_pipeline.message_bus import bootstrap
    from .console import Console

    # Bootstrap the pump
    pump = await bootstrap(config_path)

    # Create and run console
    console = Console(pump)

    # Start pump in background
    pump_task = asyncio.create_task(pump.run())

    try:
        await console.run()
    finally:
        # Cleanup
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass
        await pump.shutdown()

    print("Goodbye!")


if __name__ == "__main__":
    # Find config
    args = sys.argv[1:]
    if args:
        config_path = args[0]
    else:
        # Use bundled config
        config_path = str(Path(__file__).parent / "organism.yaml")

    if not Path(config_path).exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)

    try:
        asyncio.run(main(config_path))
    except KeyboardInterrupt:
        print("\nInterrupted")
