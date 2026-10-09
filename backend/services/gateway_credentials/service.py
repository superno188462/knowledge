"""Persist and validate user-owned Gateway keys for context and vector access."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken
from services.auth.database import get_session_factory
from services.gateway_credentials.context_client import GatewayContextClient
from services.gateway_credentials.models import GatewayUserCredential
from services.rag.gateway_vector_store import GatewayVectorStore, GatewayVectorStoreError


class GatewayCredentialError(RuntimeError):
    """Safe credential/configuration error code for the API layer."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GatewayCredentialService:
    """Store encrypted keys by verified Identity Center user ID and bind Gateway clients."""

    @staticmethod
    def _fernet() -> Fernet:
        secret = os.getenv("GATEWAY_CREDENTIAL_ENCRYPTION_KEY", "").strip()
        if not secret:
            raise GatewayCredentialError("GATEWAY_CREDENTIAL_ENCRYPTION_KEY_REQUIRED")
        try:
            return Fernet(secret.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise GatewayCredentialError("GATEWAY_CREDENTIAL_ENCRYPTION_KEY_INVALID") from error

    @staticmethod
    async def _row(user_id: str) -> GatewayUserCredential | None:
        factory = get_session_factory()
        async with factory() as session:
            return await session.get(GatewayUserCredential, user_id)

    async def configured_key_info(self, user_id: str) -> dict[str, str] | None:
        row = await self._row(user_id)
        if row is None:
            return None
        return {"last_four": row.key_last_four, "updated_at": row.updated_at.isoformat()}

    async def get_key(self, user_id: str) -> str:
        row = await self._row(user_id)
        if row is None:
            raise GatewayCredentialError("GATEWAY_API_KEY_REQUIRED")
        try:
            return self._fernet().decrypt(row.encrypted_api_key.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError) as error:
            raise GatewayCredentialError("GATEWAY_CREDENTIAL_DECRYPTION_FAILED") from error

    @staticmethod
    def vector_store(api_key: str) -> GatewayVectorStore:
        return GatewayVectorStore(api_key=api_key)

    @staticmethod
    def context_client(api_key: str) -> GatewayContextClient:
        return GatewayContextClient(api_key)

    async def validate(self, api_key: str) -> dict[str, bool]:
        self._fernet()
        if not api_key.strip() or api_key != api_key.strip():
            raise GatewayCredentialError("GATEWAY_API_KEY_INVALID_FORMAT")
        try:
            collections = await self.vector_store(api_key).list_collections()
        except GatewayVectorStoreError as error:
            if error.code == "VECTOR_GATEWAY_AUTH_FAILED":
                raise GatewayCredentialError("GATEWAY_API_KEY_INVALID") from error
            if error.code == "VECTOR_GATEWAY_SERVICE_NOT_ENABLED":
                raise GatewayCredentialError("GATEWAY_VECTOR_SERVICE_NOT_ENABLED") from error
            if error.code == "VECTOR_STORE_NOT_CONFIGURED":
                raise GatewayCredentialError("GATEWAY_VECTOR_CONFIGURATION_REQUIRED") from error
            if error.code in {"VECTOR_GATEWAY_TIMEOUT", "VECTOR_GATEWAY_NETWORK_ERROR"}:
                raise GatewayCredentialError(error.code) from error
            raise GatewayCredentialError(error.code) from error
        collection = (os.getenv("GATEWAY_VECTOR_COLLECTION") or os.getenv("GATEWAY_COLLECTION", "")).strip()
        return {
            "vector_access": True,
            "collection_exists": any(item.get("collection") == collection for item in collections),
        }

    async def save_key(self, user_id: str, api_key: str) -> dict[str, str]:
        encrypted = self._fernet().encrypt(api_key.encode("utf-8")).decode("ascii")
        factory = get_session_factory()
        async with factory.begin() as session:
            row = await session.get(GatewayUserCredential, user_id, with_for_update=True)
            if row is None:
                row = GatewayUserCredential(
                    user_id=user_id,
                    encrypted_api_key=encrypted,
                    key_last_four=api_key[-4:],
                )
                session.add(row)
            else:
                row.encrypted_api_key = encrypted
                row.key_last_four = api_key[-4:]
            await session.flush()
            await session.refresh(row)
            return {"last_four": row.key_last_four, "updated_at": row.updated_at.isoformat()}

    async def delete_key(self, user_id: str) -> None:
        factory = get_session_factory()
        async with factory.begin() as session:
            row = await session.get(GatewayUserCredential, user_id, with_for_update=True)
            if row is not None:
                await session.delete(row)

    async def access_status(self, user_id: str) -> dict[str, object]:
        info = await self.configured_key_info(user_id)
        if info is None:
            return {"configured": False, "valid": False}
        try:
            key = await self.get_key(user_id)
            capabilities = await self.validate(key)
            return {"configured": True, "valid": True, **info, **capabilities}
        except GatewayCredentialError as error:
            return {"configured": True, "valid": False, **info, "error_code": error.code}

    async def delete_for_user(self, user_id: str) -> None:
        await self.delete_key(user_id)

    async def get_context_client_for_user(self, user_id: str) -> GatewayContextClient:
        # Context data is user-scoped, but Gateway authentication uses a server-held
        # project key. A user's own key remains a fallback for local development.
        api_key = os.getenv("GATEWAY_CONTEXT_API_KEY", "").strip()
        if not api_key:
            try:
                api_key = await self.get_key(user_id)
            except GatewayCredentialError as error:
                if error.code != "GATEWAY_API_KEY_REQUIRED":
                    raise
                raise GatewayCredentialError("GATEWAY_CONTEXT_API_KEY_REQUIRED") from error
        return self.context_client(api_key)

    async def get_vector_store_for_user(self, user_id: str) -> GatewayVectorStore:
        return self.vector_store(await self.get_key(user_id))


gateway_credential_service = GatewayCredentialService()
