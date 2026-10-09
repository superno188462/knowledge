"""旧模型导入兼容；所有表仍使用同一个 Base。"""
from app.modules.knowledge.models import KnowledgeDocument, KnowledgeChunk, KnowledgeDirectory
from app.modules.embedding_jobs.models import KnowledgeEmbeddingJob
from app.modules.activity_logs.models import KnowledgeActivityLog
