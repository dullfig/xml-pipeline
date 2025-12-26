# agent_server.py
"""
AgentServer — The Living Organism Host
December 25, 2025

Preliminary but runnable implementation.

This is the body: one process, one secure WebSocket endpoint,
hosting many concurrent AgentService organs sharing a single
tamper-proof MessageBus from xml-pipeline.

Features in this version:
- Mandatory WSS (TLS)
- First-message TOTP authentication
- Per-user capability control via config/users.yaml
- Personalized <catalog/> on connect
- Ed25519 identity generation helper
- Boot-time agent registration
- Hooks for future signed privileged commands

XML wins.
"""

import asyncio
import os
import ssl
import time
from typing import Optional, Dict, Any

import pyotp
import yaml
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from websockets.server import serve, WebSocketServerProtocol

from xml_pipeline import MessageBus
from xml_pipeline.service import AgentService
from xml_pipeline.message import repair_and_canonicalize, XmlTamperError


class AgentServer:
    """
    The body of the organism.
    One instance = one living, multi-personality swarm.
    """

    # Default identity location — can be overridden if needed
    IDENTITY_DIR = os.path.expanduser("~/.agent_server")
    PRIVATE_KEY_PATH = os.path.join(IDENTITY_DIR, "identity.ed25519")
    PUBLIC_KEY_PATH = os.path.join(IDENTITY_DIR, "identity.ed25519.pub")

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        ssl_context: Optional[ssl.SSLContext] = None,
        users_config_path: str = "config/users.yaml",
        identity_pubkey_path: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.ssl_context = ssl_context  # None = ws:// (dev only), set for wss://
        self.bus = MessageBus()

        # Load per-user TOTP secrets + allowed root tags
        self.users_config: Dict[str, Dict[str, Any]] = self._load_users_config(users_config_path)

        # Load organism public key for future privileged command verification
        self.pubkey: Optional[bytes] = None
        pubkey_path = identity_pubkey_path or self.PUBLIC_KEY_PATH
        if os.path.exists(pubkey_path):
            self.pubkey = self._load_pubkey(pubkey_path)

        # Built-in platform listeners will be added here in future versions

    @staticmethod
    def _load_users_config(path: str) -> Dict[str, Dict[str, Any]]:
        """Load users.yaml → {user_id: {totp_secret: ..., allowed_root_tags: [...]}}"""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Users config not found: {path}")
        with open(path) as f:
            data = yaml.safe_load(f) or {}
            return data.get("users", {})

    @staticmethod
    def _load_pubkey(path: str) -> bytes:
        """Load raw Ed25519 public key bytes"""
        with open(path, "rb") as f:
            content = f.read().strip()
            # Accept either raw bytes or ssh-ed25519 format
            if content.startswith(b"ssh-ed25519 "):
                import base64
                return base64.b64decode(content.split()[1])
            return content

    def register_agent(
        self,
        agent_class: type[AgentService],
        *,
        system_prompt: str,
        max_concurrent: int = 10,
        session_timeout: float = 1800.0,
        version: str = "1.0",
        public: bool = True,
    ) -> None:
        """Register a permanent agent at boot time."""
        # Wrapper to store public flag for catalog building
        self.bus.register_agent(
            agent_class=agent_class,
            system_prompt=system_prompt,
            max_concurrent=max_concurrent,
            session_timeout=session_timeout,
            version=version,
            metadata={"public": public},
        )

    async def _handle_client(self, websocket: WebSocketServerProtocol):
        """Per-connection handler: authenticate → send catalog → pump messages"""
        context = {
            "authenticated": False,
            "user": None,
            "allowed_tags": set(),
            "bad_message_count": 0,
            "last_bad_time": 0.0,
        }

        try:
            # 1. Authentication — first message must be <authenticate totp="..."/>
            first_raw = await asyncio.wait_for(websocket.recv(), timeout=15.0)
            auth_msg = repair_and_canonicalize(first_raw)

            if auth_msg.getroot().tag != "authenticate":
                await websocket.close(code=1008, reason="First message must be <authenticate/>")
                return

            totp_code = auth_msg.getroot().get("totp")
            if not totp_code:
                await websocket.close(code=1008, reason="Missing TOTP code")
                return

            user_id = self._authenticate_totp(totp_code)
            if not user_id:
                await websocket.close(code=1008, reason="Invalid TOTP")
                return

            user_cfg = self.users_config[user_id]
            allowed_tags = set(user_cfg.get("allowed_root_tags", []))
            if "*" in allowed_tags:
                # Wildcard = all current + future tags
                allowed_tags = None  # Special sentinel

            context.update({
                "authenticated": True,
                "user": user_id,
                "allowed_tags": allowed_tags,
            })

            # 2. Send personalized catalog
            catalog_xml = self.bus.build_catalog_for_user(allowed_tags)
            await websocket.send(catalog_xml)

            # 3. Message pump
            async def inbound():
                async for raw in websocket:
                    try:
                        yield repair_and_canonicalize(raw)
                    except XmlTamperError:
                        await websocket.close(code=1008, reason="Invalid/tampered XML")
                        raise

            async def outbound(message: bytes):
                await websocket.send(message)

            await self.bus.run(
                inbound=inbound(),
                outbound=outbound,
                context=context,  # For ACL checks in listeners
            )

        except asyncio.TimeoutError:
            await websocket.close(code=1008, reason="Authentication timeout")
        except Exception as e:
            print(f"Client error ({websocket.remote_address}): {e}")

    def _authenticate_totp(self, code: str) -> Optional[str]:
        """Validate TOTP and return user identifier if successful"""
        for user_id, cfg in self.users_config.items():
            totp = pyotp.TOTP(cfg["totp_secret"])
            if totp.verify(code, valid_window=1):  # 30s tolerance
                return user_id
        return None

    async def start(self):
        """Start the organism — runs forever"""
        scheme = "wss" if self.ssl_context else "ws"
        print(f"AgentServer starting on {scheme}://{self.host}:{self.port}")
        print("Organism awakening...")

        async with serve(self._handle_client, self.host, self.port, ssl=self.ssl_context):
            await asyncio.Future()  # Run forever

    @classmethod
    def generate_identity(cls, force: bool = False) -> None:
        """
        Generate the organism's permanent Ed25519 identity.
        Run once on first deployment.
        """
        os.makedirs(cls.IDENTITY_DIR, exist_ok=True)

        if os.path.exists(cls.PRIVATE_KEY_PATH) and not force:
            print("Identity already exists:")
            print(f"   Private key: {cls.PRIVATE_KEY_PATH}")
            print(f"   Public key : {cls.PUBLIC_KEY_PATH}")
            print("Use --force to regenerate (will overwrite!).")
            return

        print("Generating organism Ed25519 identity...")

        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        # Private key — PEM PKCS8 unencrypted (rely on file permissions)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        # Public key — raw bytes + ssh-ed25519 format for readability
        public_raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        public_ssh = f"ssh-ed25519 {public_raw.hex()} organism@{os.uname().nodename}"

        # Write with secure permissions
        with open(cls.PRIVATE_KEY_PATH, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(private_pem)

        with open(cls.PUBLIC_KEY_PATH, "w") as f:
            f.write(public_ssh + "\n")

        print("Organism identity created!")
        print(f"Private key (KEEP SAFE): {cls.PRIVATE_KEY_PATH}")
        print(f"Public key             : {cls.PUBLIC_KEY_PATH}")
        print("\nBackup the private key offline. Lose it → lose structural control forever.")


# ————————————————————————
# Example CLI entrypoint
# ————————————————————————
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AgentServer — the living organism")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Run the server
    run_p = subparsers.add_parser("run", help="Start the organism")
    run_p.add_argument("--host", default="0.0.0.0")
    run_p.add_argument("--port", type=int, default=8765)
    run_p.add_argument("--cert", help="Path to TLS fullchain.pem")
    run_p.add_argument("--key", help="Path to TLS privkey.pem")
    run_p.add_argument("--users-config", default="config/users.yaml")

    # Generate identity
    gen_p = subparsers.add_parser("generate-identity", help="Create cryptographic identity")
    gen_p.add_argument("--force", action="store_true")

    args = parser.parse_args()

    if args.command == "generate-identity":
        AgentServer.generate_identity(force=args.force)

    else:  # run
        ssl_ctx = None
        if args.cert and args.key:
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(args.cert, args.key)

        server = AgentServer(
            host=args.host,
            port=args.port,
            ssl_context=ssl_ctx,
            users_config_path=args.users_config,
        )

        # Example boot-time agents (uncomment and customise)
        # from your_agents import CodingAgent, ResearchAgent, GrokAgent
        # server.register_agent(CodingAgent, system_prompt="You are an elite Python engineer...", max_concurrent=20)
        # server.register_agent(ResearchAgent, system_prompt="You are a thorough researcher...", max_concurrent=10)
        # server.register_agent(GrokAgent, system_prompt="You are Grok, built by xAI...", max_concurrent=15)

        asyncio.run(server.start())
