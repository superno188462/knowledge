"""Persist and validate user-owned Gateway keys for context and vector access."""

from __future__ import annotations


from cryptography.fernet import Fernet, InvalidToken
from app.integrations.gateway.context import GatewayContextClient
from app.modules.credentials.models import GatewayUserCredential
from .repository import CredentialRepository
from app.integrations.gateway.vector_store import GatewayVectorStore
from app.integrations.gateway.errors import GatewayVectorStoreError
from app.dependencies import build_gateway_client
from app.settings import get_settings


class GatewayCredentialError(RuntimeError):
    """Safe credential/configuration error code for the API layer."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GatewayCredentialService:
    """Store encrypted keys by verified Identity Center user ID and bind Gateway clients."""

    def __init__(self, repository: CredentialRepository | None = None):
        self.repository = repository or CredentialRepository()

    @staticmethod
    def _fernet() -> Fernet:
        secret = get_settings().credential_encryption_key
        if not secret:
            raise GatewayCredentialError("GATEWAY_CREDENTIAL_ENCRYPTION_KEY_REQUIRED")
        try:
            return Fernet(secret.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise GatewayCredentialError("GATEWAY_CREDENTIAL_ENCRYPTION_KEY_INVALID") from error

    async def _row(self, user_id: str) -> GatewayUserCredential | None:
        return await self.repository.get(user_id)

    async def configured_key_info(self, user_id: str) -> dict[str, object] | None:
        row = await self._row(user_id)
        if row is None:
            return None
        return {"last_four": row.key_last_four, "updated_at": row.updated_at.isoformat(),
                "selected_collection": row.selected_collection, "selected_collection_id": row.selected_collection_id}

    async def get_key(self, user_id: str) -> str:
        row = await self._row(user_id)
        if row is None:
            raise GatewayCredentialError("GATEWAY_API_KEY_REQUIRED")
        try:
            return self._fernet().decrypt(row.encrypted_api_key.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError) as error:
            raise GatewayCredentialError("GATEWAY_CREDENTIAL_DECRYPTION_FAILED") from error

    @staticmethod
    def vector_store(api_key: str, collection: str = "") -> GatewayVectorStore:
        return build_gateway_client(api_key, collection=collection).vectors

    @staticmethod
    def context_client(api_key: str) -> GatewayContextClient:
        return build_gateway_client(api_key).context

    async def validate(self, api_key: str) -> dict[str, object]:
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
        return {"vector_access": True, "collections": collections,
                "collection_exists": False, "knowledge_ready": False}

    async def save_key(self, user_id: str, api_key: str) -> dict[str, str]:
        encrypted = self._fernet().encrypt(api_key.encode("utf-8")).decode("ascii")
        return await self.repository.save(user_id, encrypted, api_key[-4:])

    async def delete_key(self, user_id: str) -> None:
        await self.repository.delete(user_id)

    async def access_status(self, user_id: str) -> dict[str, object]:
        info = await self.configured_key_info(user_id)
        if info is None:
            return {"configured": False, "valid": False}
        try:
            key = await self.get_key(user_id)
            capabilities = await self.validate(key)
            selected = next((item for item in capabilities["collections"]
                             if item.get("collection") == info.get("selected_collection")
                             and (not info.get("selected_collection_id")
                                  or str(item.get("id")) == info["selected_collection_id"])), None)
            return {"configured": True, "valid": True, **info, **capabilities,
                    "collection_exists": selected is not None,
                    "knowledge_ready": selected is not None}
        except GatewayCredentialError as error:
            return {"configured": True, "valid": False, **info, "error_code": error.code}

    async def delete_for_user(self, user_id: str) -> None:
        await self.delete_key(user_id)

    async def get_context_client_for_user(self, user_id: str) -> GatewayContextClient:
        # Context data is user-scoped, but Gateway authentication uses a server-held
        # project key. A user's own key remains a fallback for local development.
        api_key = get_settings().context_api_key
        if not api_key:
            try:
                api_key = await self.get_key(user_id)
            except GatewayCredentialError as error:
                if error.code != "GATEWAY_API_KEY_REQUIRED":
                    raise
                raise GatewayCredentialError("GATEWAY_CONTEXT_API_KEY_REQUIRED") from error
        return self.context_client(api_key)

    async def get_vector_store_for_user(self, user_id: str) -> GatewayVectorStore:
        info = await self.configured_key_info(user_id)
        if not info or not info.get("selected_collection"):
            raise GatewayCredentialError("GATEWAY_COLLECTION_NOT_SELECTED")
        return self.vector_store(await self.get_key(user_id), str(info["selected_collection"]))

    async def select_collection(self, user_id: str, collection: str) -> dict[str, object]:
        """选择前重新验证 Key 可访问的集合，不接受列表以外的名称。"""
        access = await self.validate(await self.get_key(user_id))
        selected = next((item for item in access["collections"] if item.get("collection") == collection), None)
        if selected is None:
            raise GatewayCredentialError("GATEWAY_COLLECTION_NOT_ACCESSIBLE")
        await self.repository.select_collection(user_id, collection, str(selected["id"]) if selected.get("id") else None)
        info = await self.configured_key_info(user_id)
        return {"configured": True, "valid": True, **(info or {}), **access,
                "collection_exists": True, "knowledge_ready": True}


gateway_credential_service = GatewayCredentialService()

