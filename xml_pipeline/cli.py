"""
xml-pipeline CLI entry point.

Usage:
    xml-pipeline run [config.yaml]     Run an organism
    xml-pipeline serve [config.yaml]   Run organism with API server
    xml-pipeline init [name]           Create new organism config
    xml-pipeline check [config.yaml]   Validate config without running
    xml-pipeline version               Show version info
    xml-pipeline keygen [-o path]      Generate Ed25519 identity keypair
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


def cmd_serve(args: argparse.Namespace) -> int:
    """Run an organism with the AgentServer API."""
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn not installed.", file=sys.stderr)
        print("Install with: pip install xml-pipeline[server]", file=sys.stderr)
        return 1

    from xml_pipeline.message_bus import bootstrap

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        return 1

    async def run_with_server():
        """Bootstrap pump and run with server."""
        from xml_pipeline.server import create_app

        # Bootstrap the pump
        pump = await bootstrap(str(config_path))

        # Create FastAPI app
        app = create_app(pump)

        # Run uvicorn
        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        # Run pump and server concurrently
        pump_task = asyncio.create_task(pump.run())

        try:
            await server.serve()
        finally:
            await pump.shutdown()
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass

    try:
        print(f"Starting AgentServer on http://{args.host}:{args.port}")
        print(f"  API docs: http://{args.host}:{args.port}/docs")
        print(f"  WebSocket: ws://{args.host}:{args.port}/ws")
        asyncio.run(run_with_server())
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


def cmd_keygen(args: argparse.Namespace) -> int:
    """Generate Ed25519 identity keypair or TOTP secret."""
    if args.totp:
        return _keygen_totp(args)
    return _keygen_ed25519(args)


def _keygen_ed25519(args: argparse.Namespace) -> int:
    """Generate Ed25519 identity keypair."""
    from xml_pipeline.crypto import generate_identity

    output = Path(args.output)

    # Prevent overwrite unless forced
    if output.exists() and not args.force:
        print(f"Error: {output} already exists. Use --force to overwrite.", file=sys.stderr)
        return 1

    public_path = output.with_suffix(".pub")
    if public_path.exists() and not args.force:
        print(f"Error: {public_path} already exists. Use --force to overwrite.", file=sys.stderr)
        return 1

    try:
        identity = generate_identity()
        identity.save(output, public_path)

        print(f"Generated Ed25519 identity keypair:")
        print(f"  Private key: {output}")
        print(f"  Public key:  {public_path}")
        print()
        print(f"Add to organism.yaml:")
        print(f"  organism:")
        print(f"    identity: \"{output}\"")
        print()
        print("IMPORTANT: Keep the private key secure. Never commit it to version control.")
        return 0
    except Exception as e:
        print(f"Error generating keys: {e}", file=sys.stderr)
        return 1


def _keygen_totp(args: argparse.Namespace) -> int:
    """Generate TOTP secret for OOB channel authentication."""
    from xml_pipeline.crypto.totp import generate_secret, get_provisioning_uri

    name = args.name or "organism"
    secret = generate_secret()
    uri = get_provisioning_uri(secret, name)

    print(f"Generated TOTP secret for OOB authentication:")
    print(f"  Secret: {secret}")
    print(f"  Provisioning URI: {uri}")
    print()
    print(f"Add to .env:")
    print(f"  ORGANISM_TOTP_SECRET={secret}")
    print()
    print(f"Add to organism.yaml:")
    print(f"  auth:")
    print(f"    totp_secret_env: ORGANISM_TOTP_SECRET")
    print(f"    totp_required: true")
    print()
    print("Scan the provisioning URI with your authenticator app (Google Authenticator, Authy, etc.).")
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

    # serve
    serve_parser = subparsers.add_parser("serve", help="Run organism with API server")
    serve_parser.add_argument("config", nargs="?", default="organism.yaml", help="Config file")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    serve_parser.add_argument("--port", "-p", type=int, default=8080, help="Port to listen on (default: 8080)")
    serve_parser.set_defaults(func=cmd_serve)

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

    # keygen
    keygen_parser = subparsers.add_parser("keygen", help="Generate Ed25519 identity keypair or TOTP secret")
    keygen_parser.add_argument(
        "-o", "--output",
        default="identity.key",
        help="Output path for private key (default: identity.key)",
    )
    keygen_parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing")
    keygen_parser.add_argument(
        "--totp", action="store_true",
        help="Generate TOTP secret instead of Ed25519 keypair",
    )
    keygen_parser.add_argument(
        "--name", default=None,
        help="Organism name for TOTP provisioning URI",
    )
    keygen_parser.set_defaults(func=cmd_keygen)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
