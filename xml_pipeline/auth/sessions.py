"""
Session management with token-based authentication.

Tokens are random hex strings stored in memory with expiry.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


# Default session lifetime
DEFAULT_SESSION_LIFETIME = timedelta(hours=8)

# Token length in bytes (32 bytes = 64 hex chars)
TOKEN_BYTES = 32


@dataclass
class Session:
    """An authenticated session."""
    token: str
    username: str
    role: str
    created_at: datetime
    expires_at: datetime
    last_activity: datetime
    
    def is_expired(self) -> bool:
        """Check if session has expired."""
        return datetime.now(timezone.utc) > self.expires_at
    
    def touch(self) -> None:
        """Update last activity time."""
        self.last_activity = datetime.now(timezone.utc)
    
    def to_dict(self) -> dict:
        """Convert to dict for API responses."""
        return {
            "token": self.token,
            "username": self.username,
            "role": self.role,
            "expires_at": self.expires_at.isoformat(),
        }


class SessionManager:
    """
    Manages authenticated sessions.
    
    Thread-safe for concurrent access.
    
    Usage:
        manager = SessionManager()
        
        # Create session after successful login
        session = manager.create("admin", "admin")
        
        # Validate token on subsequent requests
        session = manager.validate(token)
        if session:
            print(f"Welcome back {session.username}")
        
        # Logout
        manager.revoke(token)
    """
    
    def __init__(self, lifetime: timedelta = DEFAULT_SESSION_LIFETIME):
        self.lifetime = lifetime
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
    
    def create(
        self,
        username: str,
        role: str,
        lifetime: Optional[timedelta] = None,
    ) -> Session:
        """
        Create a new session.
        
        Args:
            username: Authenticated username
            role: User's role
            lifetime: Optional custom lifetime
        
        Returns:
            New Session with token
        """
        token = secrets.token_hex(TOKEN_BYTES)
        now = datetime.now(timezone.utc)
        expires = now + (lifetime or self.lifetime)
        
        session = Session(
            token=token,
            username=username,
            role=role,
            created_at=now,
            expires_at=expires,
            last_activity=now,
        )
        
        with self._lock:
            self._sessions[token] = session
            self._cleanup_expired()
        
        return session
    
    def validate(self, token: str) -> Optional[Session]:
        """
        Validate a session token.
        
        Args:
            token: Session token from client
        
        Returns:
            Session if valid, None if invalid/expired
        """
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None
            
            if session.is_expired():
                del self._sessions[token]
                return None
            
            session.touch()
            return session
    
    def revoke(self, token: str) -> bool:
        """
        Revoke a session (logout).
        
        Returns:
            True if session was revoked, False if not found
        """
        with self._lock:
            if token in self._sessions:
                del self._sessions[token]
                return True
            return False
    
    def revoke_user(self, username: str) -> int:
        """
        Revoke all sessions for a user.
        
        Returns:
            Number of sessions revoked
        """
        with self._lock:
            to_revoke = [
                token for token, session in self._sessions.items()
                if session.username == username
            ]
            for token in to_revoke:
                del self._sessions[token]
            return len(to_revoke)
    
    def get_user_sessions(self, username: str) -> list[Session]:
        """Get all active sessions for a user."""
        with self._lock:
            return [
                s for s in self._sessions.values()
                if s.username == username and not s.is_expired()
            ]
    
    def _cleanup_expired(self) -> None:
        """Remove expired sessions. Must hold lock."""
        expired = [
            token for token, session in self._sessions.items()
            if session.is_expired()
        ]
        for token in expired:
            del self._sessions[token]
    
    def active_count(self) -> int:
        """Count active sessions."""
        with self._lock:
            self._cleanup_expired()
            return len(self._sessions)


# Global instance
_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get the global session manager."""
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
