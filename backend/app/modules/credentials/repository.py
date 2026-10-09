"""Knowledge 本地加密凭据持久化；不进行 Gateway 调用。"""
from app.infrastructure.database import get_session_factory
from .models import GatewayUserCredential


class CredentialRepository:
    async def get(self, user_id: str) -> GatewayUserCredential | None:
        async with get_session_factory()() as session:
            return await session.get(GatewayUserCredential, user_id)

    async def save(self, user_id: str, encrypted_key: str, last_four: str) -> dict[str, str]:
        async with get_session_factory().begin() as session:
            row = await session.get(GatewayUserCredential, user_id, with_for_update=True)
            if row is None:
                row = GatewayUserCredential(user_id=user_id, encrypted_api_key=encrypted_key,
                                            key_last_four=last_four)
                session.add(row)
            else:
                row.encrypted_api_key = encrypted_key
                row.key_last_four = last_four
            row.selected_collection = None
            row.selected_collection_id = None
            await session.flush()
            await session.refresh(row)
            return {"last_four": row.key_last_four, "updated_at": row.updated_at.isoformat()}

    async def delete(self, user_id: str) -> None:
        async with get_session_factory().begin() as session:
            row = await session.get(GatewayUserCredential, user_id, with_for_update=True)
            if row is not None:
                await session.delete(row)

    async def select_collection(self, user_id: str, collection: str, collection_id: str | None) -> None:
        """保存用户选择，不保存任何前端传入的身份信息。"""
        async with get_session_factory().begin() as session:
            row = await session.get(GatewayUserCredential, user_id, with_for_update=True)
            if row is None:
                raise ValueError("Gateway Key 尚未配置")
            row.selected_collection = collection
            row.selected_collection_id = collection_id
