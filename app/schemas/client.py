from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class MofslClientCreate(BaseModel):
    name: str
    client_id: str
    api_key: str
    api_secret: str
    totp_secret: Optional[str] = None
    password_hash: str
    two_fa: Optional[str] = None
    capital: float = 0.0
    qty_multiplier: float = 1.0
    copy_enabled: bool = True


class MofslClientUpdate(BaseModel):
    name: Optional[str] = None
    client_id: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    totp_secret: Optional[str] = None
    password_hash: Optional[str] = None
    two_fa: Optional[str] = None
    capital: Optional[float] = None
    qty_multiplier: Optional[float] = None
    copy_enabled: Optional[bool] = None
    is_active: Optional[bool] = None


class MofslClientOut(BaseModel):
    id: int
    user_id: int
    name: str
    client_id: str
    api_key: str
    api_secret: str
    totp_secret: Optional[str] = None
    two_fa: Optional[str] = None
    capital: float
    qty_multiplier: float
    copy_enabled: bool
    is_active: bool
    is_live: bool
    token_expiry: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ClientGroupCreate(BaseModel):
    name: str
    multiplier: float = 1.0
    member_ids: List[int] = []


class ClientGroupOut(BaseModel):
    id: int
    user_id: int
    name: str
    multiplier: float
    members: List[MofslClientOut] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupMemberUpdate(BaseModel):
    member_ids: List[int]
