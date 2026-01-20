"""
Authentication and authorization for xml-pipeline.

Provides:
- UserStore: User management with Argon2id password hashing
- SessionManager: Token-based session management
"""

from .users import User, UserStore, get_user_store
from .sessions import Session, SessionManager, get_session_manager

__all__ = [
    "User",
    "UserStore",
    "get_user_store",
    "Session",
    "SessionManager",
    "get_session_manager",
]
