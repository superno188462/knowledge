# Knowledge Docker 部署

## 组成

```text
浏览器 → HTTPS 入口（宿主机 Nginx/Caddy）
                    ↓ 127.0.0.1:3000
              web：Nginx + React 静态文件
                    ↓ /api 同源转发
              api：FastAPI + 内置异步 worker
                 ├── PostgreSQL：业务数据、队列、日志、加密 Key
                 ├── 身份中心：账号及会话验证
                 └── Gateway：向量、画像和记忆
```

SDK 安装在后端镜像中，不单独运行。镜像构建使用 `uv.lock` 和 `--no-editable`，运行时不挂载 SDK 源码。
`migrate` 是一次性数据库迁移容器，成功后才启动 API；不会删除或重置数据库。
默认不发布数据库/API 端口，只将 web 绑定宿主机 `127.0.0.1:3000`。
API 只有一个进程，内置默认 3 个并发文件任务，切片在线程池执行。

## 服务器准备

使用 Linux 服务器，安装 Docker Engine、Buildx、Docker Compose 插件（建议 Compose 2.24.4 或以上）。
本方案不需要服务器安装 Node.js/Python/uv，构建镜像会安装所需依赖。

仓库须先提交、推送后才可在服务器获取；本轮没有推送。
两个目录必须相邻，Compose 额外构建上下文读取 `../gateway_api`：

```bash
mkdir -p /opt/knowledge-stack
cd /opt/knowledge-stack
git clone git@github.com:superno188462/gateway_api.git gateway_api
git clone git@github.com:superno188462/knowledge.git knowledge
cd knowledge
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
```

Docker 的 localhost 是容器自身。身份中心/Gateway 如果在同一宿主机，需要使用容器可达地址或共享 Docker 网络中的服务名，不能填写 `localhost`。

## 配置与首次启动

修改 `deploy/.env`：

- `POSTGRES_PASSWORD`：设置随机十六进制密码，避免连接 URL 中的特殊字符；可用 `openssl rand -hex 32` 生成。
- `CORS_ALLOW_ORIGINS`：填写浏览器访问的完整来源，如 `https://knowledge.example.com`，不要带尾斜杠。
- `IDENTITY_CENTER_BASE_URL`：后端可达的身份中心根地址，不追加 `/api/auth`。
- `GATEWAY_BASE_URL` / `GATEWAY_VECTOR_BASE_URL`：真实 Gateway 的 `/v1` 地址，保留实际代理前缀，例如 `/gateway/v1`。
- `GATEWAY_EMBEDDING_MODEL`：可选的 SDK 默认完整模型名；用户在页面选择已有集合或创建集合时填写模型。无需设置固定 collection。
- `GATEWAY_CONTEXT_API_KEY`：服务端用户上下文 Key。知识库向量 Key 仍由用户在设置页面提交。
- `GATEWAY_CREDENTIAL_ENCRYPTION_KEY`：有效 Fernet Key；迁移已有数据库时必须使用原来的值。

所有配置只供后端使用，真实 `deploy/.env` 被 Git 和 Docker 构建上下文排除。
前端构建使用空 API 地址，浏览器只向同一站点 `/api` 请求，不包含任何 Key。

```bash
docker compose --env-file deploy/.env build
```

新部署可用构建完成的镜像生成 Fernet Key，复制输出到 `deploy/.env`：

```bash
docker compose --env-file deploy/.env run --rm --no-deps api \
  uv run --no-sync python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

该生成命令不启动 API，不连接数据库。不要为已有数据库更换加密 Key，否则已保存的 Gateway Key 无法解密。

```bash
docker compose --env-file deploy/.env up -d
docker compose --env-file deploy/.env ps -a
docker compose --env-file deploy/.env logs --tail=100 migrate api
```

`migrate` 显示 `Exited (0)` 是正常状态。若迁移失败，先查看日志修复，不会启动 API。
容器健康检查只说明 API 进程可响应，不代表身份中心或 Gateway 已验证可用。

## HTTPS 与认证

在宿主机 Nginx/Caddy 配置域名 HTTPS，转发到 `http://127.0.0.1:3000`。
已有证书的 Nginx 站点示例：

```nginx
server {
    listen 443 ssl;
    server_name knowledge.example.com;
    ssl_certificate /etc/letsencrypt/live/knowledge.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/knowledge.example.com/privkey.pem;
    client_max_body_size 20m;
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
server {
    listen 80;
    server_name knowledge.example.com;
    return 301 https://$host$request_uri;
}
```

证书与域名配置由服务器提供，本方案不自动签发证书。
浏览器登录请求由 Knowledge API 代理身份中心，设置本站 `knowledge_identity_session` HttpOnly Cookie。
后续由 Knowledge 后端向身份中心验证会话，因此不需要浏览器跨站共享身份中心 Cookie，也不宣称是跨站 OIDC SSO。
生产配置使用 `AUTH_COOKIE_SECURE=true`、`AUTH_COOKIE_SAMESITE=lax`，访问地址必须是 HTTPS。

若仅临时用 HTTP IP 测试，应同时改 `AUTH_COOKIE_SECURE=false`、`CORS_ALLOW_ORIGINS=http://服务器IP:3000`，
并设置 `KNOWLEDGE_BIND_ADDRESS=0.0.0.0`。恢复 HTTPS 时再恢复 Secure Cookie 和 loopback 绑定。

## 使用已有 PostgreSQL

默认 Compose 创建全新的 Knowledge 数据库，不会自动搬迁本地文档和日志。
如需继续使用现有数据，保留原加密 Key，并在 `deploy/.env` 增加：

```dotenv
EXTERNAL_DATABASE_URL=postgresql+asyncpg://USER:URL_ENCODED_PASSWORD@DB_HOST:5432/knowledge
```

启用外部数据库覆盖配置；此后所有 Compose 命令都使用相同两个 `-f` 参数：

```bash
docker compose --env-file deploy/.env -f compose.yaml -f deploy/compose.external-db.yaml up -d --build
```

该模式不启动内置 db 容器，迁移直接连接外部数据库。迁移前备份现有数据库。
容器需要实际能访问 DB_HOST；数据库账号必须具备本项目 schema 的迁移权限。

## 更新、日志与备份

本节以默认内置数据库模式为例。外部数据库模式增加前述 `-f` 参数。

1. 在两个仓库分别取得明确版本，记录提交 SHA；不要在服务器丢弃本地配置。
2. 备份数据库和部署配置，然后构建新镜像。
3. 停止 API，重新执行迁移，再启动 API/web。

```bash
# Linux shell 中备份；文件包含业务数据，应按部署目录的访问权限保存。
docker compose --env-file deploy/.env exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > knowledge-backup.dump
docker compose --env-file deploy/.env build
docker compose --env-file deploy/.env stop web api
docker compose --env-file deploy/.env up -d --force-recreate migrate
docker compose --env-file deploy/.env up -d api web
```

API 启动会等待迁移成功。保留旧镜像/代码版本便于应用回滚；数据库迁移需按迁移脚本单独评估回滚，不能仅回退镜像。
PostgreSQL 数据保存在命名卷 `knowledge_postgres_data`。普通 `docker compose down` 保留数据，`down -v` 会删除卷，日常停止不要加 `-v`。
外部 Gateway 保存的向量不在此 PostgreSQL 备份中。

```bash
docker compose --env-file deploy/.env logs -f --tail=100 api
docker compose --env-file deploy/.env logs --tail=100 migrate web
```

操作日志仍在网页“操作日志”页面和 Knowledge 数据库中。终端日志设置轮转（每个容器 3 个 10MB 文件）。
修改端口只需调整 `KNOWLEDGE_HTTP_PORT`，修改域名需同步 `CORS_ALLOW_ORIGINS` 和 HTTPS 入口；前端镜像无需重新注入域名。

## 当前交付状态

已提供镜像、Compose、同源反向代理和迁移编排配置。
本地 Docker 引擎未运行，未实际构建镜像、启动容器或验证服务器连接；真实部署后的登录、Gateway 和数据库连通性需要在目标服务器确认。

## 构建规范参考

- [Docker Compose 额外构建上下文](https://docs.docker.com/reference/compose-file/build/)
- [uv Docker 与非 editable 安装](https://docs.astral.sh/uv/guides/integration/docker/)
