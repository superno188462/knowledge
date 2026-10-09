"""用户上下文装配；身份必须由后端认证依赖提供。"""
from app import dependencies
from app.integrations.identity.client import IdentityUser
from app.integrations.gateway.context import GatewayContextClient

async def get_user_context_client(user: IdentityUser) -> GatewayContextClient:
    return await dependencies.get_gateway_credential_service().get_context_client_for_user(user.id)
