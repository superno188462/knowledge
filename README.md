# Knowledge 数据中台

独立的知识库管理应用：管理虚拟目录、文档原文和切片映射；每个切片由本项目生成稳定 ID 和 metadata，再写入 Agent Gateway 向量 collection。Gateway 保存向量并提供相似度检索。本项目不直接连接 Gateway 的 PostgreSQL/pgvector。

知识库工作区采用文件树、正文编辑区和切片规则检查器。可创建目录、上传/编辑/删除 `.txt` 与 `.md` 文件；上传和保存只把原文存成 Knowledge 草稿，不调用 Embedding。用户点击“嵌入”或“保存并嵌入”后，项目才切片并提交 Gateway。规则按根目录、目录、文件逐级继承，支持结构优先、固定长度和自定义分隔符；父子层级可单独启用。结构优先对 Markdown 识别标题、段落和代码块，对纯文本识别段落，再以句子和长度兜底。父子模式只向量化子块，父块保存在 Knowledge 本地，命中子块后由检索接口补充父块上下文。旧版本中没有本地原文的条目需要重新上传后才能重切片。

工作区数据结构由迁移 `20261009_01` 和 `20261009_02` 增加：显式目录规则、文件规则、可重试的新版本暂存数据、嵌入失败码以及父块上下文映射；同时取消“同一文档序号只能有一条向量映射”的限制，以便新版本切片全部写入成功后再切换活动版本。升级前备份本地数据库和 Gateway collection。迁移只变更 Knowledge 元数据表，不清理 Gateway 向量；应用代码会在成功更新后按 ID 删除旧切片。

## 运行

需要 Node.js 和 `uv`。开发环境使用两个终端。

后端：

```powershell
cd backend
Copy-Item .env.example .env
# 编辑 .env：填写独立 DATABASE_URL、Gateway URL、共享 collection 和 Gateway Key 加密密钥
uv sync
# 先检查数据库迁移版本；若早于 20261007_03，升级链会删除旧的本地账号/认证表，需先备份并确认迁移方案
uv run alembic current
uv run alembic upgrade head
uv run python main.py
```

前端本地使用根目录 `.env.example`；生产构建可参考 `.env.production.example`。后端本地使用 `backend/.env.example`；生产配置可参考 `backend/.env.production.example`。这些文件只有占位值，不应写入真实数据库密码、加密密钥或 Gateway Key。Gateway Key 由登录用户在应用“设置”中提交，后端验证后加密保存。

前端：

```powershell
Copy-Item .env.example .env.local
npm install
npm start
```

后端默认监听 `127.0.0.1:8001`。需要指定其他端口时运行 `uv run python main.py 8010`。前端执行 `npm start` 默认监听 `http://localhost:3001`。Knowledge 后端通过 `IDENTITY_CENTER_BASE_URL` 服务端访问统一身份中心；浏览器只请求 Knowledge API，不直接读取身份 Cookie，也不保存密码或令牌到前端存储。

### Gateway Key、知识库和用户上下文

登录后可以直接进入“用户上下文”查看自己的资料；该接口由后端使用身份中心验证得到的 `user.id` 隔离数据。管理员配置服务端 `GATEWAY_CONTEXT_API_KEY` 后，普通用户无需拥有 Gateway Key 即可查看和维护自己的画像、长期记忆及短期会话；若未配置，则回退使用该用户在设置中保存的 Key。设置中的用户 Key 只验证向量服务权限，用于开放知识库管理。后端必须在 `GATEWAY_CREDENTIAL_ENCRYPTION_KEY` 配置 Fernet 密钥，可用 `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 生成；丢失此密钥后，数据库中的用户 Key 无法解密，用户需重新提交。

登录后始终显示“用户上下文”和“设置”；只有 Key 验证通过时才显示“知识库”。知识库页面和对应 API 都要求当前身份中心账号已有可用 Key。用户上下文页面只把经身份中心 `/api/auth/me` 验证得到的 `user.id` 作为 Gateway `external_user_id`，支持查看/更新画像，以及分页查看、新增、修改和删除长期记忆。每个用户的知识库 Key 属于 Gateway 项目级权限，额度仍按 Gateway 项目共享；密钥不会传给前端。

知识库文档操作和向量检索使用当前登录用户保存的 Key；用户上下文管理优先使用服务端 `GATEWAY_CONTEXT_API_KEY`，未配置时回退到该用户保存的 Key。所有 Key 应属于共享 collection/上下文数据所在的 Gateway 项目；Gateway 当前没有返回 Key 项目归属标识的只读接口，Knowledge 无法自动验证 Key 所属项目 ID。首次尚未创建 collection 时，仍允许使用已验证的用户 Key 进入知识库初始化。

数据库迁移 `20261008_04` 会新增 `gateway_user_credentials` 表，保存加密后的用户 Key、Key 末四位和更新时间。迁移只新增表；降级会删除用户已保存的 Gateway Key。

### 统一身份中心

Knowledge 的注册、登录、登出和当前账号查询由 `backend/app.py` 代理到 Identity Center。身份中心返回的 opaque 会话值由 Knowledge 后端作为 `knowledge_identity_session` HttpOnly、host-only Cookie 返回给浏览器；后续 Knowledge API 请求携带该 Cookie，后端再向身份中心 `/api/auth/me` 验证，并只使用响应中的 `user.id` 作为当前用户 ID。密码只在注册/登录请求期间由后端转发，不写入 Knowledge 数据库或日志。身份服务不可用时 API 返回 `IDENTITY_CENTER_UNAVAILABLE`，不会把它当成匿名用户继续处理。

本地配置：在 `backend/.env` 设置 `IDENTITY_CENTER_BASE_URL=http://127.0.0.1:8002`。前端浏览器访问的 API 地址由根目录 `.env.local` 的 `REACT_APP_KNOWLEDGE_API_HOST` 指定；默认 `http://localhost:8001`。后端 `CORS_ALLOW_ORIGINS` 必须逐字包含 `http://localhost:3001`（或你实际使用的 Origin）。身份中心 CORS 不参与该 BFF 调用；浏览器只跨域请求 Knowledge API。

生产环境需单独设置：`IDENTITY_CENTER_BASE_URL` 为 Knowledge 后端可访问的身份中心 HTTPS/API 地址；`CORS_ALLOW_ORIGINS` 为浏览器地址栏中 Knowledge 前端的精确 Origin；`AUTH_COOKIE_SECURE=true` 且优先使用 `AUTH_COOKIE_SAMESITE=lax`。前端与 API 即使端口不同，也应部署在同一站点；建议通过同站反向代理将 `/api` 路由到 Knowledge 后端。若浏览器把 API 视为跨站第三方请求，`SameSite=None; Secure` 仍可能受浏览器第三方 Cookie 策略阻止，不能视为可靠部署方案。Knowledge 后端必须能通过受信任网络/TLS 访问身份中心。当前两个仓库都没有 Nginx 配置，生产域名未在仓库中定义，因此上线前需由部署配置填入真实值。

该代理方案可在前端与 Knowledge API 分属不同域名时工作：身份 Cookie 绑定到 Knowledge API 域名，后端把 opaque 值转发给身份中心验证。它不提供跨应用免登录；身份中心当前未实现 OIDC Authorization Code + PKCE，语音项目等其他应用仍需自己的可信代理登录流程或等待 OIDC。

## Gateway 共享约定

多个业务应用要读写同一 collection，必须使用**同一个 Gateway 项目**下分别签发的 API Key，并设置相同的 collection 名称、Embedding 模型和向量维度。Gateway 按项目隔离；不同 Gateway 项目即使 collection 同名也互不可见。每个 Key 都具有其 Gateway 项目级别的访问范围。

knowledge 管理端新增切片时强制写入：

```json
{
  "app_id": "knowledge-platform",
  "record_type": "knowledge_chunk",
  "document_id": "内部稳定文档 ID",
  "owner_user_id": "Identity Center 验证后的 user.id",
  "source": "目录/文件名.md",
  "chunk_index": 0,
  "chunk_count": 3,
  "path": "目录/文件名.md",
  "chunk_sha256": "...",
  "metadata_schema_version": 1
}
```

`source` 是 collection 根目录下规范化的相对路径，使用 `/` 分隔；每条切片都独立携带该值。更新文档时，Knowledge 先写入内容/规则对应的新一代向量 ID，成功后再清理旧 ID。失败时保留此前已活动的向量，并在编辑器中展示失败状态及上一个已嵌入正文。Gateway 没有跨批次事务，因此批量删除旧记录若遇网络中断仍存在部分成功的可能；详情见工作区设计文档。

Knowledge 的队列后台按文件并行处理，Gateway 客户端按每批最多 64 条切片发送 upsert，单次请求超时为 120 秒。后端核对 Gateway 返回的 `upserted[].external_id`；全部确认后才清理旧向量、切换活动版本。若请求超时，Gateway 可能已经写入该条记录，Knowledge 将其标记为“写入结果未确认”，保留旧活动版本与本地切片映射以供后续处理。重新嵌入使用当前登录用户保存在 Knowledge 后端的 Gateway 项目 Key。

嵌入队列保存在 Knowledge 的 PostgreSQL `knowledge_embedding_jobs` 表。升级后端前执行 `cd backend`、`uv run alembic upgrade head`；随后以 `uv run python main.py` 启动，命令会同时启动独立 worker 进程来领取任务，HTTP API 不再执行切片或 Gateway 向量写入。可选环境变量 `KNOWLEDGE_EMBEDDING_WORKERS=3` 控制同时处理的文件数（允许 1–16）。多个 worker 使用 `FOR UPDATE SKIP LOCKED` 领取不同文件任务，不再争抢一个全局 PostgreSQL 会话锁；运行中每 10 秒更新任务心跳，180 秒未续约的任务可由其他 worker 重新领取，尝试序号阻止旧 worker 覆盖新任务状态。每个文件的向量生命周期仍有独立同步锁，等待超过 30 秒会明确失败并写入日志，不会无限卡住。若通过 `uvicorn app:app` 启动 API，须另行运行 `uv run python worker.py`；`main.py` 可设置 `KNOWLEDGE_START_EMBEDDING_WORKER=0` 关闭自动拉起 worker。

点击“保存并嵌入”或“重试已保存草稿”会先保存正文，再立即返回任务 ID 与 `queued` 状态。`GET /api/knowledge-base/embedding-jobs` 可按当前登录用户查看最近任务，单个任务可通过 `GET /api/knowledge-base/embedding-jobs/{job_id}` 查询。状态依次为 `queued`、`running`、`succeeded` 或 `failed`；阶段包括 `checking`、`chunking`、`embedding`、`deleting_old`。Gateway 的向量 upsert 接口会在保存文本时生成 Embedding，没有单独提交预计算向量的接口，因此“生成并保存新向量”是同一个阶段。Knowledge 在查询结果中只展示本地标记为活动的切片，写入中的新一代切片不会提前作为检索结果返回。

文件树和文档详情只读 Knowledge 本地数据库，不随轮询请求 Gateway；集合状态单独查询，前端按状态响应中的目标摘要判断是否需要重新嵌入。任务状态轮询串行执行，避免慢请求累积。

### 操作日志

升级后端时先在 `backend` 目录运行 `uv run alembic upgrade head`，新增的 `knowledge_activity_logs` 表才会生效。登录后可从顶部“操作日志”进入独立页面，即使 Gateway Key 暂时失效也能查看本地历史。页面同时显示最近的嵌入任务，排队超过 2 分钟会提示检查 worker；历史日志按当前用户隔离，支持按事件类型和文件路径筛选、分页与自动刷新。`GET /api/knowledge-base/activity-logs?page=1&page_size=25` 返回同样的数据；可选 `event_type`、`path_query`、`document_id`。日志记录草稿保存、目录/文件规则修改、移动、删除、嵌入排队、每批确认后对应的切片进度以及最终成功/失败；保存路径、ID、数量、错误码及 Gateway HTTP 状态/Trace ID，不保存文档正文、切片文本或 Gateway Key。文档删除后，日志仍保留。日志从升级之后的新操作开始记录，不回填旧任务。

Knowledge 会记录每次成功嵌入所对应的 Gateway 地址和 collection ID 的摘要。切换 Gateway 地址、项目 Key 所属项目，或重建同名 collection 后，旧文档会显示“当前网关待嵌入”；点击文件的嵌入按钮才会向新目标写入，避免未经确认就消耗 Embedding 额度。更换同一 Gateway 项目下的 Key，若 collection ID 不变，则不会重复嵌入。旧目标的向量无法在新目标中删除，切换后旧目标的数据需在旧 Gateway 单独清理。升级前已嵌入的文档缺少目标标识，会保守地提示重新嵌入一次；原有向量在完成迁移前仍保留。

切片规则使用 `strategy=fixed|delimiter`、`chunk_size`、`overlap`；`delimiter` 策略额外设置 `delimiter` 字面分隔符，输入 `\\n` 表示换行。无论规则如何设置，每个切片都不能超过 collection 的最大字符长度，超长 delimiter 分段会再按长度拆分。

语音 Agent 的 RAG 只过滤 `app_id=knowledge-platform`、`record_type=knowledge_chunk`。未来其他项目的长期记忆向量应带 `app_id`、`record_type=long_term_memory`、业务用户稳定 ID 和 `memory_type`；不能与公共知识切片混查。长期记忆向量写入目前尚未接入，语音项目仍使用 Gateway Context 文本记忆。

Gateway 当前支持相似度查询和 metadata 精确过滤，但没有列出 collection 全量向量的接口。知识文档和切片目录按身份中心 `user.id` 隔离；检索会由后端强制添加 `owner_user_id`，忽略客户端提供的同名字段。Gateway metadata 过滤属于业务侧隔离约定，不是 Gateway 的行级 ACL。详细边界见[共享向量数据约定](设计文档/数据中台与共享向量数据约定.md)。

## 账号与数据迁移

本项目不再维护本地用户名、密码或本地会话。执行 `uv run alembic upgrade head` 会移除 Knowledge 本地认证表（其中包括本地密码哈希与会话），原本地账号需要在 Identity Center 注册/登录；该账号表迁移无法恢复旧凭据。知识文档与切片映射不会被删除；旧文档的 `owner_user_id` 保持 `NULL`，不会自动分配给某个新身份，也不会出现在用户目录或其个人检索结果中。确认旧数据归属后，需由维护者制定逐条归属与 Gateway metadata 回填方案，再恢复其可见性；不要仅凭相同用户名自动绑定。

执行数据库迁移前，建议先对本项目数据库和 Gateway collection 做常规备份。迁移回滚会重建空的本地认证表结构，不会恢复已移除的旧用户名、密码哈希或会话；知识文档、切片和向量仍保留。不要用回滚恢复旧本地登录代码。

向量客户端配置、批处理、重试与兼容性见 [Gateway 向量接入说明](backend/docs/gateway-vector-integration.md)。新配置支持 `GATEWAY_VECTOR_BASE_URL`、`GATEWAY_VECTOR_API_KEY`、`GATEWAY_VECTOR_COLLECTION` 和 `GATEWAY_EMBEDDING_MODEL`，兼容原配置。
