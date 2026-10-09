# 后端模块结构与迁移边界

后端已从单个 `app.py` 拆成 `app/` 包。业务 API 路径、请求/响应契约、数据库表名和迁移版本保持兼容；本次重构不需要新增数据库迁移。启动方式仍为 `uv run python main.py [port]`，独立 worker 为 `uv run python worker.py`。

```text
backend/
├── main.py                    # API 启动（单进程）
├── worker.py                  # 独立 worker 启动与终端日志配置
├── app/
│   ├── bootstrap.py           # FastAPI、CORS、路由注册
│   ├── settings.py            # 读取 .env 和统一配置
│   ├── dependencies.py        # Gateway/身份客户端和业务服务装配
│   ├── infrastructure/
│   │   └── database.py        # 唯一 ORM Base、连接池、Session
│   ├── integrations/
│   │   ├── gateway/           # 可整体复制，统一入口 GatewayClient
│   │   └── identity/client.py # 身份中心客户端
│   └── modules/
│       ├── auth/              # 登录、Cookie 验证、Origin、安全边界
│       ├── credentials/       # Key 验证、加密保存、数据库访问
│       ├── knowledge/         # 文档/目录、切片、metadata、版本和检索
│       ├── embedding_jobs/    # PostgreSQL 队列、租约、心跳、后台处理
│       ├── user_context/      # 当前已验证用户的上下文路由与装配
│       └── activity_logs/     # 用户隔离、分页、持久操作日志
├── services/                  # 旧导入路径的薄兼容桥；新代码使用 app/
├── migrations/
└── tests/
```

HTTP 路由、请求 Schema、业务 Service、持久化 Repository 和 ORM Models 按模块放置。仅有必要的模块保留这些文件，纯认证不另设本地账号表。Gateway 的 HTTP 错误由业务模块的 `errors.py` 转换成面向前端的响应，SDK 不依赖 FastAPI。

## 操作日志

日志是业务功能，完整保留在 `activity_logs/`。`models.py` 定义 `knowledge_activity_logs`；`repository.py` 按用户读取并过滤；`service.py` 将日志写入现有业务事务；`router.py` 暴露原 `/api/knowledge-base/activity-logs`。文档保存/删除和任务领取/进度/失败继续记录日志，文件删除不会级联删除日志。

嵌入按确认批次更新进度，同时为该批每条切片产生日志；最终失败包含安全错误码、HTTP 状态和 Trace ID。内置 worker 使用 API 日志配置，独立运行时由 `worker.py` 配置终端日志。SDK 本身不会连接 Knowledge 日志表，其他项目应接入自己的业务审计。

## 可复用与项目专属

整个 `app/integrations/gateway/` 是可移植 SDK，仅依赖 httpx。它不包含 `.env`、数据库凭据或用户密钥获取逻辑。使用方法见该目录 README。

`credentials`、`knowledge`、`embedding_jobs` 和 `activity_logs` 属于 Knowledge，使用本项目的 PostgreSQL、身份归属和生命周期；不能随 SDK 一起无条件复制到其他业务项目。

`services/` 暂时仅保留导入兼容桥，避免外部脚本立即失效；应用内部和新测试使用新模块。旧向量/上下文客户端入口保留从 Knowledge 配置装配的兼容方式，SDK 本身改为构造参数配置。后续确认所有外部脚本已迁移后，可以删除兼容目录。

## 验证与重启

```powershell
uv run pytest -q
uv run python main.py --help
uv run alembic current
```

适配器测试全部使用 Mock；独立复制验证不连接生产 Gateway。路由基线检查对比重构前 API，日志测试确认后端固定使用已验证用户 ID。重构后须重启 API 和 worker，正在运行的旧进程不会自动加载新模块。

## 单进程异步任务

FastAPI lifespan 启动并持有 embedding_queue.serve() 后台任务，默认开启，无需单独运行 worker.py；关闭时取消任务及心跳。任务领取、进度和日志使用异步数据库操作，Gateway 使用 httpx.AsyncClient；切片、切片哈希及记录生成通过 asyncio.to_thread() 在线程池执行。KNOWLEDGE_EMBEDDING_WORKERS 限制并发文件数（默认 3）。数据库任务表与租约恢复逻辑保持不变，无需数据库迁移。若选择独立 worker.py，应设置 KNOWLEDGE_START_EMBEDDING_WORKER=0。

## 独立 Gateway SDK（当前接入方式）

SDK 已迁至相邻仓库 `gateway_api/src/gateway_client/`，Knowledge 使用 `agent-gateway-client` 的 editable 依赖。原 `app/integrations/gateway/` 仅兼容旧导入，原目录复制和独立打包方式已被替代。完整开发与部署说明见 `backend/docs/gateway-sdk-workflow.md`（后端文档中相对路径为 `docs/gateway-sdk-workflow.md`）。
