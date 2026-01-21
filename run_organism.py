#!/usr/bin/env python3
"""
run_organism.py — Deprecated entry point.

The TUI console and server have been moved to the Nextra SaaS product.
This file is kept for backwards compatibility but will display a helpful message.

For the open-source xml-pipeline, use the CLI or programmatic API:

    # CLI
    xml-pipeline run config/organism.yaml

    # Programmatic
    from xml_pipeline.message_bus import bootstrap
    pump = await bootstrap("organism.yaml")
    await pump.run()

    # Interactive console example
    pip install xml-pipeline[console]
    python -m examples.console

For the full TUI console with authentication and WebSocket server,
see the Nextra project.
"""

import sys


def main() -> None:
    """Show deprecation message and exit."""
    print("""
xml-pipeline: TUI Console Moved to Nextra
==========================================

The interactive TUI console with authentication and WebSocket server
has been moved to the Nextra SaaS product (v0.4.0).

For the open-source xml-pipeline, use:

  1. CLI command:
     xml-pipeline run config/organism.yaml

  2. Programmatic API:
     from xml_pipeline.message_bus import bootstrap
     pump = await bootstrap("organism.yaml")
     await pump.run()

  3. Console example (for testing):
     pip install xml-pipeline[console]
     python -m examples.console

For full TUI console, authentication, and WebSocket server features,
see the Nextra project.
""")
    sys.exit(1)


if __name__ == "__main__":
    main()
