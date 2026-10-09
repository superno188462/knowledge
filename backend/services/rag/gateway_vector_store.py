"""旧导入兼容；新项目请复制 app/integrations/gateway 并使用 GatewayClient。"""
from app.integrations.gateway.vector_store import GatewayVectorStore as _VectorStore
from app.integrations.gateway.errors import GatewayVectorStoreError
from app.settings import get_settings

class GatewayVectorStore(_VectorStore):
    def __init__(self, base_url=None, api_key=None, collection=None, **options):
        settings = get_settings()
        super().__init__(base_url if base_url is not None else settings.vector_base_url,
                         api_key if api_key is not None else settings.vector_api_key,
                         collection if collection is not None else settings.collection, **options)
        self.embedding_model = settings.embedding_model

def get_gateway_vector_store():
    return GatewayVectorStore()
