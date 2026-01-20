"""
xml-pipeline CLI entry point.

Usage:
    xml-pipeline run [config.yaml]     Run an organism
    xml-pipeline init [name]           Create new organism config
    xml-pipeline check [config.yaml]   Validate config without running
    xml-pipeline version               Show version info
"""

import argparse
import asyncio
import sys
from pathlib import Path


def cmd_run(args: argparse.Namespace) -> int:
    """Run an organism from config."""
    from xml_pipeline.config.loader import load_config
    from xml_pipeline.message_bus import bootstrap

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        return 1

    try:
        config = load_config(config_path)
        asyncio.run(bootstrap(config))
        return 0
    except KeyboardInterrupt:
        print("\nShutdown requested.")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize a new organism config."""
    from xml_pipeline.config.template import create_organism_template

    name = args.name or "my-organism"
    output = Path(args.output or f"{name}.yaml")

    if output.exists() and not args.force:
        print(f"Error: {output} already exists. Use --force to overwrite.", file=sys.stderr)
        return 1

    template = create_organism_template(name)
    output.write_text(template)
    print(f"Created {output}")
    print(f"\nNext steps:")
    print(f"  1. Edit {output} to configure your agents")
    print(f"  2. Set your LLM API key: export XAI_API_KEY=...")
    print(f"  3. Run: xml-pipeline run {output}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Validate config without running."""
    from xml_pipeline.config.loader import load_config, ConfigError

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        return 1

    try:
        config = load_config(config_path)
        print(f"Config valid: {config.organism.name}")
        print(f"  Listeners: {len(config.listeners)}")
        print(f"  LLM backends: {len(config.llm_backends)}")

        # Check optional features
        from xml_pipeline.config.features import check_features
        features = check_features(config)
        if features.missing:
            print(f"\nOptional features needed:")
            for feature, reason in features.missing.items():
                print(f"  pip install xml-pipeline[{feature}]  # {reason}")

        return 0
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1


def cmd_version(args: argparse.Namespace) -> int:
    """Show version and feature info."""
    from xml_pipeline import __version__
    from xml_pipeline.config.features import get_available_features

    print(f"xml-pipeline {__version__}")
    print()
    print("Installed features:")
    for feature, available in get_available_features().items():
        status = "yes" if available else "no"
        print(f"  {feature}: {status}")
    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="xml-pipeline",
        description="Tamper-proof nervous system for multi-agent organisms",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run
    run_parser = subparsers.add_parser("run", help="Run an organism")
    run_parser.add_argument("config", nargs="?", default="organism.yaml", help="Config file")
    run_parser.set_defaults(func=cmd_run)

    # init
    init_parser = subparsers.add_parser("init", help="Create new organism config")
    init_parser.add_argument("name", nargs="?", help="Organism name")
    init_parser.add_argument("-o", "--output", help="Output file path")
    init_parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing")
    init_parser.set_defaults(func=cmd_init)

    # check
    check_parser = subparsers.add_parser("check", help="Validate config")
    check_parser.add_argument("config", nargs="?", default="organism.yaml", help="Config file")
    check_parser.set_defaults(func=cmd_check)

    # version
    version_parser = subparsers.add_parser("version", help="Show version info")
    version_parser.set_defaults(func=cmd_version)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
