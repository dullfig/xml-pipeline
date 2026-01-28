#!/usr/bin/env python3
"""
run_organism.py — Deprecated entry point.

The TUI console has been moved to OpenBlox (https://openblox.ai).
This file is kept for backwards compatibility but will display a helpful message.

For the open-source xml-pipeline, use the CLI or programmatic API:

    # CLI - run organism
    xml-pipeline run config/organism.yaml

    # CLI - run with API server
    xml-pipeline serve config/organism.yaml --port 8080

    # Programmatic
    from xml_pipeline.message_bus import bootstrap
    pump = await bootstrap("organism.yaml")
    await pump.run()

    # Interactive console example
    pip install xml-pipeline[console]
    python -m examples.console

For the full TUI console with authentication, see OpenBlox.
"""

import sys


def main() -> None:
    """Show deprecation message and exit."""
    print("""
xml-pipeline: TUI Console Moved to OpenBlox
============================================

The interactive TUI console with authentication has been moved to
OpenBlox (https://openblox.ai).

For the open-source xml-pipeline, use:

  1. CLI command (organism only):
     xml-pipeline run config/organism.yaml

  2. CLI with API server:
     xml-pipeline serve config/organism.yaml --port 8080

  3. Programmatic API:
     from xml_pipeline.message_bus import bootstrap
     pump = await bootstrap("organism.yaml")
     await pump.run()

  4. Console example (for testing):
     pip install xml-pipeline[console]
     python -m examples.console

For full TUI console and authentication features, see OpenBlox.
""")
    sys.exit(1)


if __name__ == "__main__":
    main()
