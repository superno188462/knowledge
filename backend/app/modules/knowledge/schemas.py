"""HTTP 请求与响应契约。"""
from typing import Any
from pydantic import BaseModel, Field, SecretStr

class KnowledgeBaseInitializeRequest(BaseModel):
    description: str = Field(default="共享知识库数据中台", max_length=1000)
    embedding_model: str = Field(min_length=1, max_length=200)
    chunk_size: int = Field(default=1000, ge=100, le=6000)


class KnowledgeDocumentRequest(BaseModel):
    document_id: str | None = Field(default=None, min_length=1, max_length=200)
    directory: str = Field(default="", max_length=1000)
    name: str | None = Field(default=None, min_length=1, max_length=500)
    source_type: str | None = None
    file_name: str | None = Field(default=None, min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=200_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunking_config: dict[str, Any] | None = Field(
        default=None,
        description="文件级切片规则覆盖；null 表示继承目录规则。",
    )


class KnowledgeDirectoryRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000, description="相对于知识库根目录的目录路径。")


class KnowledgeChunkingRuleRequest(BaseModel):
    path: str = Field(max_length=1000, description="需要设置规则的目录相对路径；空字符串代表根目录。")
    rule: dict[str, Any] | None = Field(description="目录规则；null 表示移除本层规则并继承上级。")


class DocumentChunkingRuleRequest(BaseModel):
    rule: dict[str, Any] | None = Field(description="文件规则；null 表示继承目录规则。")


class KnowledgeDocumentMoveRequest(BaseModel):
    target_path: str = Field(min_length=1, max_length=1000, description="新的相对文件路径。")


class KnowledgeDocumentBatchRequest(BaseModel):
    documents: list[KnowledgeDocumentRequest] = Field(min_length=1, max_length=64)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    top_k: int = Field(default=5, ge=1, le=20)
    metadata: dict[str, Any] | None = None


