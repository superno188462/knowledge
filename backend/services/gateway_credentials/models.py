"""Encrypted Gateway API keys owned by verified Identity Center users."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from services.auth.database import Base


class GatewayUserCredential(Base):
    """One encrypted Gateway project key per canonical user ID."""

    __tablename__ = "gateway_user_credentials"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
