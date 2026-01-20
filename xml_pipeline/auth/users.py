"""
User store with Argon2id password hashing.

Users are stored in ~/.xml-pipeline/users.yaml with hashed passwords.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


CONFIG_DIR = Path.home() / ".xml-pipeline"
USERS_FILE = CONFIG_DIR / "users.yaml"


@dataclass
class User:
    """A user account."""
    username: str
    password_hash: str
    role: str = "operator"  # admin, operator, viewer
    created_at: str = ""
    last_login: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "password_hash": self.password_hash,
            "role": self.role,
            "created_at": self.created_at,
            "last_login": self.last_login,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> User:
        return cls(
            username=data["username"],
            password_hash=data["password_hash"],
            role=data.get("role", "operator"),
            created_at=data.get("created_at", ""),
            last_login=data.get("last_login"),
        )


class UserStore:
    """
    Manages user accounts with secure password storage.
    
    Usage:
        store = UserStore()
        store.create_user("admin", "secretpass", role="admin")
        
        user = store.authenticate("admin", "secretpass")
        if user:
            print(f"Welcome {user.username}!")
    """
    
    def __init__(self, users_file: Path = USERS_FILE):
        self.users_file = users_file
        self.hasher = PasswordHasher()
        self._users: dict[str, User] = {}
        self._load()
    
    def _ensure_dir(self) -> None:
        """Create config directory if needed."""
        self.users_file.parent.mkdir(parents=True, exist_ok=True)
    
    def _load(self) -> None:
        """Load users from file."""
        if not self.users_file.exists():
            return
        try:
            with open(self.users_file) as f:
                data = yaml.safe_load(f) or {}
            for username, user_data in data.get("users", {}).items():
                user_data["username"] = username
                self._users[username] = User.from_dict(user_data)
        except Exception:
            pass
    
    def _save(self) -> None:
        """Save users to file."""
        self._ensure_dir()
        
        data = {
            "users": {
                username: {
                    "password_hash": user.password_hash,
                    "role": user.role,
                    "created_at": user.created_at,
                    "last_login": user.last_login,
                }
                for username, user in self._users.items()
            }
        }
        
        with open(self.users_file, "w") as f:
            yaml.dump(data, f, default_flow_style=False)
        
        # Set file permissions to 600
        if sys.platform != "win32":
            os.chmod(self.users_file, stat.S_IRUSR | stat.S_IWUSR)
    
    def has_users(self) -> bool:
        """Check if any users exist."""
        return len(self._users) > 0
    
    def get_user(self, username: str) -> Optional[User]:
        """Get user by username."""
        return self._users.get(username)
    
    def list_users(self) -> list[str]:
        """List all usernames."""
        return list(self._users.keys())
    
    def create_user(
        self,
        username: str,
        password: str,
        role: str = "operator",
    ) -> User:
        """
        Create a new user.
        
        Args:
            username: Unique username
            password: Plain text password (will be hashed)
            role: User role (admin, operator, viewer)
        
        Returns:
            The created User
        
        Raises:
            ValueError: If username already exists
        """
        if username in self._users:
            raise ValueError(f"User already exists: {username}")
        
        if len(password) < 4:
            raise ValueError("Password must be at least 4 characters")
        
        user = User(
            username=username,
            password_hash=self.hasher.hash(password),
            role=role,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        
        self._users[username] = user
        self._save()
        return user
    
    def authenticate(self, username: str, password: str) -> Optional[User]:
        """
        Authenticate user with password.
        
        Returns:
            User if authentication successful, None otherwise
        """
        user = self._users.get(username)
        if not user:
            return None
        
        try:
            self.hasher.verify(user.password_hash, password)
            
            # Update last login
            user.last_login = datetime.now(timezone.utc).isoformat()
            self._save()
            
            return user
        except VerifyMismatchError:
            return None
    
    def change_password(self, username: str, new_password: str) -> bool:
        """Change user's password."""
        user = self._users.get(username)
        if not user:
            return False
        
        if len(new_password) < 4:
            raise ValueError("Password must be at least 4 characters")
        
        user.password_hash = self.hasher.hash(new_password)
        self._save()
        return True
    
    def delete_user(self, username: str) -> bool:
        """Delete a user."""
        if username not in self._users:
            return False
        
        del self._users[username]
        self._save()
        return True
    
    def set_role(self, username: str, role: str) -> bool:
        """Change user's role."""
        user = self._users.get(username)
        if not user:
            return False
        
        user.role = role
        self._save()
        return True


# Global instance
_store: Optional[UserStore] = None


def get_user_store() -> UserStore:
    """Get the global user store."""
    global _store
    if _store is None:
        _store = UserStore()
    return _store
