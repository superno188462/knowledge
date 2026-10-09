# Knowledge Data Platform API

后端提供知识库管理 API，并通过服务端 BFF 代理统一身份中心认证。虚拟目录、文档/切片映射保存到本项目自己的 PostgreSQL；向量生成、保存和相似度检索经 `services/rag/gateway_vector_store.py` 调用 Agent Gateway。

## 环境

```powershell
Copy-Item .env.example .env
```

填写 `DATABASE_URL`、`IDENTITY_CENTER_BASE_URL`、`GATEWAY_BASE_URL`、`GATEWAY_COLLECTION` 和 `GATEWAY_CREDENTIAL_ENCRYPTION_KEY`。用户在 Knowledge 设置页提交自己的 Gateway 项目 Key；后端验证其向量服务权限后加密保存在本项目数据库，不放入前端或日志。若希望无需用户 Key 也能查看个人上下文，管理员还需配置服务端 `GATEWAY_CONTEXT_API_KEY`；未配置时会回退使用该用户已保存的 Key。加密密钥可用 `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 生成并安全备份。身份中心地址为后端可访问地址，不下发到浏览器。

## 初始化和运行

数据库迁移前先检查当前版本：

```powershell
uv run alembic current
```

只有数据库已处于 `20261007_03` 时，才可直接升级到新增的用户 Gateway Key 表迁移。若当前版本更早，升级链会经过 `20261007_03`，该迁移会删除旧的本地账号/认证表；先备份并确认旧账号处置方案，不要直接执行 `upgrade head`。

```powershell
uv sync
uv run alembic upgrade head
uv run python main.py
```

默认监听 `127.0.0.1:8001`。也可以把端口作为唯一参数传入，例如 `uv run python main.py 8010`；如需从局域网访问，可在 `.env` 中设置 `HOST=0.0.0.0`。

操作日志使用 PostgreSQL `knowledge_activity_logs` 表。升级时运行 `uv run alembic upgrade head`；`GET /api/knowledge-base/activity-logs` 仅返回已登录用户的文档变更和嵌入进度，支持 `page`、`page_size`、`event_type`、`path_query`、`document_id` 参数。日志不包含正文和密钥，删除文档后仍保留历史。

用户登录后可直接进入自己的用户上下文页面；知识库页面仍要求先在设置页配置有效的 Gateway 向量 Key。用户上下文请求优先使用服务端 `GATEWAY_CONTEXT_API_KEY`，并始终以身份中心验证得到的 `user.id` 作为 Gateway 用户 ID，忽略客户端用户 ID。知识库可创建/核对 collection、添加 `.txt`/`.md` 文件或粘贴内容。切片、稳定 ID、metadata、更新与删除都由本项目负责；Gateway 只接收每条预切片文本。

嵌入请求会立即进入 PostgreSQL 队列。`uv run python main.py` 会启动独立的嵌入 worker 进程；worker 有自己的事件循环和数据库连接池，默认并发处理 3 个文件，每个文件逐片上传，全部成功后切换活动版本。可通过 `KNOWLEDGE_EMBEDDING_WORKERS` 设置并发文件数。前端通过 `/api/knowledge-base/embedding-jobs` 显示排队、切片、写入和旧向量清理进度。文件树与文档详情只读本地数据库，Gateway 状态单独读取，避免嵌入时 Gateway 延迟阻塞文件管理。

如果用 `uvicorn app:app` 或进程管理器启动 API，请另起 `uv run python worker.py`。若使用 `main.py` 启动 API，但希望由进程管理器单独维护 worker，可设置 `KNOWLEDGE_START_EMBEDDING_WORKER=0`，然后单独运行 `uv run python worker.py`。多个 worker 通过数据库行锁领取任务，运行时每 10 秒续约；进程中断后，180 秒未续约的任务可被接管。旧的全局 advisory lock 不再参与调度。

完成嵌入后，本地文档与切片会保存当前 Gateway 地址和 collection ID 的摘要。目标变更时，同一正文也必须重新嵌入；前端显示“当前网关待嵌入”，需要手动提交队列。不同项目 Key 若指向同一个 Gateway collection ID，不会额外产生嵌入。旧数据仍在旧 Gateway，不能使用新 Gateway Key 自动清理。

## API

- `GET /api/knowledge-base`：collection 状态。
- `POST /api/knowledge-base/initialize`：创建/校验 collection 的 Embedding 模型和 chunk size。
- `GET /api/knowledge-base/documents`：当前身份中心用户自己的目录与同步状态。
- `POST /api/knowledge-base/documents/batch`：新增或更新虚拟路径文件。
- `DELETE /api/knowledge-base/documents?document_id=...`：删除目录项和关联向量。
- `POST /api/knowledge-base/search`：只检索当前身份中心用户拥有的 knowledge 切片。
- `GET/PUT/DELETE /api/settings/gateway-key`：验证、保存密文、读取状态（仅末四位）或移除当前用户的 Gateway Key。
- `GET/PUT /api/user-context/profile`：读取或按版本更新当前身份中心用户画像。
- `GET/POST /api/user-context/memories`、`PATCH/DELETE /api/user-context/memories/{id}`：分页管理当前用户长期记忆；更新和删除必须使用版本号。

Key 状态接口会实时验证 Gateway 向量服务与上下文服务权限。当前 Gateway 不提供 Key 对应项目 ID 的查询契约，因此所有用户 Key 需由管理员确保属于相同共享 Gateway 项目；若 Key 来自其他项目但拥有相同 collection 名称，服务端无法从现有契约识别这种错误配置。

`knowledge_identity_session` Cookie 由 Knowledge API 设置为 HttpOnly、host-only Cookie。Knowledge API 将其值作为上游 `identity_session` 转发给 Identity Center `/api/auth/me` 验证，用户 ID 只取自验证返回的 `user.id`。Identity Center 不可用时返回 503，不会伪装为未登录。

运行新迁移会清除本地账号、密码哈希和会话表；业务文档、切片映射和 Gateway 向量不会删除，但旧文档保持未分配所有者状态，等待人工制定安全归属迁移。认证表 downgrade 仅重建空表，不会恢复旧凭据。

Gateway 暂无全量向量列表接口，跨应用检索是相似度查询，不是完整数据浏览。详见项目根目录的《数据中台与共享向量数据约定》。

向量客户端配置、批处理、重试与兼容性见 [Gateway 向量接入说明](docs/gateway-vector-integration.md)。新配置支持 `GATEWAY_VECTOR_BASE_URL`、`GATEWAY_VECTOR_API_KEY`、`GATEWAY_VECTOR_COLLECTION` 和 `GATEWAY_EMBEDDING_MODEL`，兼容原配置。
