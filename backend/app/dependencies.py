"""Composition root: bind portable clients to Knowledge configuration and credentials."""
from app.settings import get_settings
from app.integrations.gateway import GatewayClient, GatewayVectorStore
from app.integrations.identity.client import IdentityCenterClient


def build_gateway_client(api_key: str | None = None, *, collection: str | None = None) -> GatewayClient:
    settings = get_settings()
    return GatewayClient(
        settings.gateway_base_url or settings.vector_base_url,
        api_key if api_key is not None else settings.vector_api_key,
        settings.collection if collection is None else collection, vector_base_url=settings.vector_base_url,
        embedding_model=settings.embedding_model,
    )


def build_vector_store(api_key: str | None = None, *, collection: str | None = None) -> GatewayVectorStore:
    return build_gateway_client(api_key, collection=collection).vectors


def get_identity_client() -> IdentityCenterClient:
    return IdentityCenterClient(base_url=get_settings().identity_base_url)


def get_gateway_credential_service():
    from app.modules.credentials.service import gateway_credential_service
    return gateway_credential_service


async def user_knowledge_service(user):
    from app.modules.credentials.service import GatewayCredentialError
    from app.modules.credentials.errors import gateway_credential_http_error
    from app.modules.knowledge.service import KnowledgeBaseService
    try:
        store = await get_gateway_credential_service().get_vector_store_for_user(user.id)
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error
    return KnowledgeBaseService(vector_store=store)
