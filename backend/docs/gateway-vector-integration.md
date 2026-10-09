# Gateway 向量保存方案

接口依据：Gateway `clients/gateway_vector_store.py`、`docs/vector-database.md`、`vector_api.py`，以及 2026-10-09 从当前服务读取的 OpenAPI。客户端保持异步 httpx 适配，与现有 FastAPI worker 集成。

## 配置

```dotenv
GATEWAY_VECTOR_BASE_URL=http://127.0.0.1:8000
GATEWAY_VECTOR_API_KEY=<仅服务端配置>
GATEWAY_VECTOR_COLLECTION=shared-knowledge
GATEWAY_EMBEDDING_MODEL=openai/text-embedding-3-small
```

根地址会补 `/v1`；部署在反向代理前缀下时可填 `https://gateway.example.com/gateway/v1`。新变量优先，未配置时兼容原 `GATEWAY_BASE_URL`、`GATEWAY_API_KEY`、`GATEWAY_COLLECTION`。Web 用户仍须配置经过验证且加密保存的个人项目 Key；HTTP 业务权限不会因服务器配置 Key 而自动开放。上下文服务继续使用自己的配置。

## 调用

```python
from app.dependencies import build_vector_store

store = build_vector_store()
await store.create_collection(chunk_size=1000)
await store.add_vectors([
    {"id": "faq-001-part-0001", "content": "已经切好的文本",
     "metadata": {"source": "faq/退款.md", "category": "售后", "chunk_index": 0}}
])
hits = await store.similarity_search("如何退款？", k=5, metadata={"category": "售后"})
await store.delete_vectors(["faq-001-part-0001"])
```

创建集合使用 `name/model/chunk_size/chunk_overlap`；已有集合模型或上限不同明确报错。模型创建后固定，维度由首条写入确定，后续由 Gateway 校验。客户端不切片。

## 文档保存与批处理

保存草稿不调用 Embedding。点击嵌入后入 PostgreSQL 队列，独立 worker 检查集合、按现有规则切片、生成稳定版本 ID，调用一次业务层 `add_vectors()`；客户端自动发送 64/64/剩余记录的批次。删除按最多 256 个 ID 分批。每项携带来源相对路径、用户归属、文档 ID、序号、哈希和业务 metadata。

每批必须完整返回输入的 `external_id` 才确认成功，进度按确认批次更新，再为该批每个切片写日志。相同文档内容、规则、metadata 和目标生成相同 ID，重试替换相同记录。修改后使用新版本 ID，保留原活动版本直到全部写入确认、旧 ID 清理完成；这是现有版本保护流程，避免跨批次部分失败覆盖正在使用的旧版本。

超时、网络错误、HTTP 502/503/504 最多重试两次（等待 0.5/1 秒）。凭据、额度、参数错误不自动重试。每次写入可能再次产生 Embedding 费用；稳定 ID 只保证记录替换，不保证上游计费去重。失败异常保存 `confirmed_ids` 和 `uncertain_ids`；超时不立即清理结果未确认的新记录，旧活动版本仍保留。其他失败尝试清理新版本，清理失败保留本地映射供重试。Gateway 各批次分别事务提交，不提供整个文件跨批次事务；旧 ID 删除也可能部分成功。

队列用任务行锁和租约领取，不再使用全局调度会话锁。任务每 10 秒续约，180 秒心跳过期可接管；文档独立同步锁最多等待 30 秒，失败记录 `DOCUMENT_SYNC_LOCK_TIMEOUT`。请重启 API 和 worker，避免旧代码仍参与调度。

## 验证

```powershell
uv run alembic upgrade head
uv run pytest -q
```

测试用 httpx MockTransport 和内存业务仓库，不触碰生产 collection。查询 metadata 按 JSONB 包含匹配，支持范围/布尔过滤表达式与否以 Gateway 当前契约为准；当前客户端仅传 metadata 对象。

可移植 SDK 使用显式构造参数和 `GatewayClient`；Knowledge 的环境变量装配位于 `app/dependencies.py`。目录迁移后请查看 [GatewayClient 使用说明](../app/integrations/gateway/README.md)。

## 独立 Gateway SDK（当前接入方式）

SDK 已迁至相邻仓库 `gateway_api/src/gateway_client/`，Knowledge 使用 `agent-gateway-client` 的 editable 依赖。原 `app/integrations/gateway/` 仅兼容旧导入，原目录复制和独立打包方式已被替代。完整开发与部署说明见 `backend/docs/gateway-sdk-workflow.md`（后端文档中相对路径为 `docs/gateway-sdk-workflow.md`）。
