"""
identity.py — Ed25519 key management for organism identity.

Each organism has a unique Ed25519 keypair:
- Private key: Stored securely, used for signing outgoing messages
- Public key: Shared with federation peers for verification

Key files use PEM format for compatibility.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

if TYPE_CHECKING:
    pass


class IdentityError(Exception):
    """Raised when identity key operations fail."""

    pass


@dataclass
class Identity:
    """
    Organism identity backed by Ed25519 keypair.

    The identity is used for:
    - Signing outgoing message envelopes
    - Verifying incoming messages from known peers
    - Federation peer authentication
    """

    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey
    path: str = ""  # Where the key was loaded from (for logging)

    @classmethod
    def load(cls, path: str | Path) -> Identity:
        """
        Load identity from a PEM-encoded private key file.

        Args:
            path: Path to the private key file (e.g., "config/identity/private.ed25519")

        Returns:
            Identity instance with loaded keypair

        Raises:
            IdentityError: If the key file doesn't exist or is invalid
        """
        key_path = Path(path)

        if not key_path.exists():
            raise IdentityError(f"Identity key not found: {path}")

        try:
            pem_data = key_path.read_bytes()
            private_key = serialization.load_pem_private_key(pem_data, password=None)

            if not isinstance(private_key, Ed25519PrivateKey):
                raise IdentityError(f"Key is not Ed25519: {path}")

            public_key = private_key.public_key()

            return cls(
                private_key=private_key,
                public_key=public_key,
                path=str(path),
            )
        except Exception as e:
            if isinstance(e, IdentityError):
                raise
            raise IdentityError(f"Failed to load identity key: {e}") from e

    @classmethod
    def generate(cls) -> Identity:
        """
        Generate a new Ed25519 identity keypair.

        Returns:
            Identity instance with freshly generated keypair
        """
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        return cls(
            private_key=private_key,
            public_key=public_key,
            path="<generated>",
        )

    def save(self, private_path: str | Path, public_path: str | Path | None = None) -> None:
        """
        Save the identity keypair to PEM files.

        Args:
            private_path: Where to save the private key
            public_path: Where to save the public key (optional, derived if not given)
        """
        private_path = Path(private_path)

        # Ensure parent directory exists
        private_path.parent.mkdir(parents=True, exist_ok=True)

        # Save private key
        private_pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        private_path.write_bytes(private_pem)

        # Save public key
        if public_path is None:
            public_path = private_path.with_suffix(".pub")
        else:
            public_path = Path(public_path)

        public_path.parent.mkdir(parents=True, exist_ok=True)

        public_pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        public_path.write_bytes(public_pem)

        self.path = str(private_path)

    def sign(self, data: bytes) -> bytes:
        """
        Sign data with the private key.

        Args:
            data: Raw bytes to sign

        Returns:
            64-byte Ed25519 signature
        """
        return self.private_key.sign(data)

    def sign_base64(self, data: bytes) -> str:
        """
        Sign data and return base64-encoded signature.

        Args:
            data: Raw bytes to sign

        Returns:
            Base64-encoded signature string
        """
        signature = self.sign(data)
        return base64.b64encode(signature).decode("ascii")

    def verify(self, signature: bytes, data: bytes) -> bool:
        """
        Verify a signature against data using our public key.

        Args:
            signature: 64-byte Ed25519 signature
            data: Original signed data

        Returns:
            True if signature is valid, False otherwise
        """
        try:
            self.public_key.verify(signature, data)
            return True
        except Exception:
            return False

    def get_public_key_pem(self) -> bytes:
        """Get the public key in PEM format for sharing."""
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def get_public_key_base64(self) -> str:
        """Get the raw public key bytes as base64 (compact format)."""
        raw_bytes = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw_bytes).decode("ascii")


def generate_identity() -> Identity:
    """
    Generate a new Ed25519 identity.

    Convenience function wrapping Identity.generate().

    Returns:
        New Identity instance
    """
    return Identity.generate()


def load_public_key(path: str | Path) -> Ed25519PublicKey:
    """
    Load a public key from a PEM file.

    Used for loading federation peer public keys.

    Args:
        path: Path to public key file

    Returns:
        Ed25519 public key

    Raises:
        IdentityError: If file doesn't exist or key is invalid
    """
    key_path = Path(path)

    if not key_path.exists():
        raise IdentityError(f"Public key not found: {path}")

    try:
        pem_data = key_path.read_bytes()
        public_key = serialization.load_pem_public_key(pem_data)

        if not isinstance(public_key, Ed25519PublicKey):
            raise IdentityError(f"Key is not Ed25519: {path}")

        return public_key
    except Exception as e:
        if isinstance(e, IdentityError):
            raise
        raise IdentityError(f"Failed to load public key: {e}") from e


def verify_with_public_key(
    public_key: Ed25519PublicKey,
    signature: bytes,
    data: bytes,
) -> bool:
    """
    Verify a signature using a public key.

    Args:
        public_key: Ed25519 public key
        signature: 64-byte signature
        data: Original signed data

    Returns:
        True if valid, False otherwise
    """
    try:
        public_key.verify(signature, data)
        return True
    except Exception:
        return False
