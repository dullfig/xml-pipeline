# agentserver/privileged/models.py
from datetime import datetime
from typing import Literal, Optional, List
from pydantic import BaseModel, Field, AnyUrl

class RegisterListener(BaseModel):
    class_path: str = Field(alias="class")
    description: Optional[str] = None
    team: Optional[str] = None
    max_concurrent: Optional[int] = None
    session_timeout: Optional[int] = None

class Shutdown(BaseModel):
    mode: Optional[Literal["graceful", "immediate"]] = None
    reason: Optional[str] = None

class RegisterRemoteGateway(BaseModel):
    url: AnyUrl
    identity: Optional[str] = None  # base64 public key
    import_tags: Optional[List[str]] = Field(default=None, alias="import-tags")
    description: Optional[str] = None
    team: Optional[str] = None
    max_concurrent: Optional[int] = None

# Union of all payload types
class PrivilegedPayload(BaseModel):
    __root__: (
        RegisterListener
        | Shutdown
        | RegisterRemoteGateway
        # ... add the rest
    )

class PrivilegedMsgEnvelope(BaseModel):
    payload: PrivilegedPayload
    signature: str   # base64 Ed25519 signature
    version: Literal["1.0"] = "1.0"