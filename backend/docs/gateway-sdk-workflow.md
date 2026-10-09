# Gateway SDK 与 Knowledge 协同开发

## 仓库关系

```text
课程/
├── gateway_api/          # Python SDK；不启动服务
│   ├── src/gateway_client/
│   ├── tests/
│   ├── pyproject.toml
│   └── uv.lock
└── knowledge/            # 业务前端、API、数据库、后台任务
    └── backend/
        ├── pyproject.toml  # ../../gateway_api 的 editable 依赖
        └── uv.lock
```

- SDK origin：`git@github.com:superno188462/gateway_api.git`
- Knowledge origin：`git@github.com:superno188462/knowledge.git`
- Knowledge 是业务应用，SDK 是调用 Gateway 服务的库；Gateway 服务端是第三个独立系统。
- SDK 包名 agent-gateway-client，导入名 gateway_client。
- 新代码可直接从 gateway_client 导入，旧 app.integrations.gateway 路径仅作转发兼容。

## 本地启动

需要将两个仓库放在同一个父目录下，保持上述目录名称：

```powershell
cd D:\github\课程\knowledge\backend
uv sync
uv run python main.py
```

另一个终端启动前端：

```powershell
cd D:\github\课程\knowledge
npm install
npm run dev
```

SDK 不需要启动独立服务。main.py 默认运行内置异步 worker，不需单独运行 worker.py。
数据库、身份中心、真实 Gateway 的地址和密钥仍在 Knowledge 的服务端 .env 中设置。
SDK 不读取 .env，不保存 Key，不包含业务身份验证、切片、队列和操作日志。

## SDK 修改

Knowledge 已使用 editable 依赖，修改 gateway_api/src/gateway_client/ 后重启 API 即可。
改变 SDK 依赖时，在 SDK 执行 uv add，并在 Knowledge 后端执行 uv lock 和 uv sync。
不要在兼容目录 app/integrations/gateway 中继续开发第二份 SDK。

其他项目后端可执行（调整相对路径）：

```powershell
uv add --editable ../../gateway_api
```

```python
from gateway_client import GatewayClient
client = GatewayClient(base_url=server_url, api_key=server_key, collection="knowledge")
hits = await client.similarity_search("问题", metadata={"record_type": "knowledge_chunk"})
```

调用项目负责从已验证身份取得用户 ID，并注入权限过滤条件。
SDK 不会自动把一个项目的数据共享给另一个项目。

## 部署与版本

当前 uv.lock 对 SDK 使用本地 editable 路径。因此单独克隆 Knowledge 后运行 uv sync 会缺少 SDK。
可以将两个仓库一起克隆到相邻目录，或者发布 SDK 后改为固定版本依赖。
本次只关联本地 origin，没有提交、推送或创建远程标签。

发布时在 SDK 中更新版本、提交并发布明确标签（例如 v0.1.0），然后 Knowledge 才可执行：

```powershell
uv add "agent-gateway-client @ git+ssh://git@github.com/superno188462/gateway_api.git@v0.1.0"
```

该命令把本地路径依赖改为 Git 标签依赖。生产机器需要仓库 SSH 读取权限。
上述标签只是发布示例，本次没有创建，不能假定它已存在。
若不希望生产机器配置 Git SSH，可在 SDK 使用 uv build，分发 wheel，再在 Knowledge 用 uv add <wheel路径>。
提交 Knowledge 的 pyproject.toml 和 uv.lock，固定 SDK 版本/提交。

## Git 使用

两个仓库分别提交、分别发布。通用 HTTP/API 改动提交到 gateway_api；业务逻辑、前端和队列改动提交到 Knowledge。
origin 已关联不代表远程内容已同步。首次推送前应 git fetch origin，查看远程分支历史，不能强制覆盖已有提交。
Knowledge 已有未提交重构改动，本次保留；SDK 仓库目前是未提交的新仓库。
