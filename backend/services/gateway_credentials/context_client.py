"""旧上下文客户端路径兼容。"""
from app.integrations.gateway.context import GatewayContextClient as _ContextClient
from app.integrations.gateway.errors import GatewayContextError
from app.settings import get_settings

class GatewayContextClient(_ContextClient):
    def __init__(self, api_key, *, base_url=None, **options):
        super().__init__(api_key, base_url=base_url if base_url is not None else get_settings().gateway_base_url, **options)
