"""嵌入任务的业务入口；领取、租约与执行在 worker 模块。"""
from .worker import EmbeddingQueue, embedding_queue
