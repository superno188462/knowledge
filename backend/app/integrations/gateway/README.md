# Gateway 客户端兼容入口

客户端实现已抽取到独立仓库 `D:\github\课程\gateway_api`，远程为
`git@github.com:superno188462/gateway_api.git`。

本目录只转发旧导入路径，不应单独复制或打包。
新项目应依赖 `agent-gateway-client`，使用 `from gateway_client import GatewayClient`。
Knowledge 后端已经通过 pyproject.toml 的本地 editable 路径引用该包。

详细说明见 `backend/docs/gateway-sdk-workflow.md`，以及 SDK 仓库 README.md。
