from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, PrimaryKeyConstraint
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class MofslClient(Base):
    __tablename__ = "mofsl_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key: Mapped[str] = mapped_column(String(255), nullable=False)
    api_secret: Mapped[str] = mapped_column(String(255), nullable=False)
    totp_secret: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    two_fa: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    capital: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    auth_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    copy_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    qty_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class ClientGroup(Base):
    __tablename__ = "client_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (PrimaryKeyConstraint("group_id", "client_id"),)

    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("client_groups.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("mofsl_clients.id", ondelete="CASCADE"), nullable=False)


class CopyTradeLog(Base):
    __tablename__ = "copy_trade_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_client_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_client_id: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    action: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    order_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
