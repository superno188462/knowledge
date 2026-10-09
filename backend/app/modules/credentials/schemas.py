"""HTTP 请求与响应契约。"""
from typing import Any
from pydantic import BaseModel, Field, SecretStr

class GatewayApiKeyRequest(BaseModel):
    """One-time submission of a user's Gateway project API Key."""

    api_key: SecretStr = Field(min_length=10, max_length=4000, description="仅写入；服务端验证后加密保存，不会返回明文。")


class GatewayCollectionRequest(BaseModel):
    """选择当前登录用户的向量目标集合。"""
    collection: str = Field(min_length=1, max_length=200, description="必须是此用户已保存 Key 可访问的集合名称。")


class GatewayCollectionCreateRequest(GatewayCollectionRequest):
    """在当前 Key 所属项目创建集合，并将它设为当前目标。"""
    embedding_model: str = Field(min_length=1, max_length=200, description="包含供应商前缀的完整模型名称。")
    chunk_size: int = Field(default=1000, ge=1, le=6000, description="单条文本长度上限，字符数。")
    description: str = Field(default="", max_length=1000, description="集合描述。")


