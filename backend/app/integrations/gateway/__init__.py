"""Knowledge 旧路径兼容层；使用独立 agent-gateway-client 包。"""
from gateway_client import (
    GatewayClient, GatewayVectorStore, GatewayContextClient,
    GatewayVectorStoreError, GatewayContextError,
)

__all__ = ["GatewayClient", "GatewayVectorStore", "GatewayContextClient",
           "GatewayVectorStoreError", "GatewayContextError"]
