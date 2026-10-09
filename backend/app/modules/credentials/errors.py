"""凭据与上下文错误的 HTTP 转换。"""
from fastapi import HTTPException
from app.modules.credentials.service import GatewayCredentialError
from app.integrations.gateway.errors import GatewayContextError

def gateway_credential_http_error(error: GatewayCredentialError | GatewayContextError) -> HTTPException:
    code = error.code
    status = {
        "GATEWAY_API_KEY_REQUIRED": 403,
        "GATEWAY_COLLECTION_NOT_SELECTED": 409,
        "GATEWAY_COLLECTION_NOT_ACCESSIBLE": 403,
        "GATEWAY_API_KEY_INVALID": 400,
        "GATEWAY_API_KEY_INVALID_FORMAT": 422,
        "GATEWAY_VECTOR_SERVICE_NOT_ENABLED": 403,
        "GATEWAY_CONTEXT_SERVICE_NOT_ENABLED": 403,
        "GATEWAY_CONTEXT_API_KEY_REQUIRED": 503,
        "GATEWAY_CONTEXT_ACCESS_DENIED": 403,
        "GATEWAY_CONTEXT_VERSION_CONFLICT": 409,
        "GATEWAY_CONTEXT_NOT_FOUND": 404,
        "GATEWAY_CREDENTIAL_ENCRYPTION_KEY_REQUIRED": 503,
        "GATEWAY_CREDENTIAL_ENCRYPTION_KEY_INVALID": 503,
        "GATEWAY_CREDENTIAL_DECRYPTION_FAILED": 503,
        "GATEWAY_BASE_URL_NOT_CONFIGURED": 503,
        "GATEWAY_VECTOR_CONFIGURATION_REQUIRED": 503,
        "GATEWAY_CONTEXT_TIMEOUT": 504,
        "VECTOR_GATEWAY_TIMEOUT": 504,
        "VECTOR_GATEWAY_NETWORK_ERROR": 503,
        "GATEWAY_CONTEXT_NETWORK_ERROR": 503,
    }.get(code, 502)
    messages = {
        "GATEWAY_COLLECTION_NOT_SELECTED": "请先在设置中选择 Gateway 集合。",
        "GATEWAY_COLLECTION_NOT_ACCESSIBLE": "当前 Key 无法访问该集合，请刷新集合列表。",
        "GATEWAY_API_KEY_REQUIRED": "请先在设置中添加 Gateway 项目 API Key。",
        "GATEWAY_API_KEY_INVALID": "Gateway API Key 无效或已撤销。",
        "GATEWAY_API_KEY_INVALID_FORMAT": "API Key 格式不正确，请检查后重试。",
        "GATEWAY_VECTOR_SERVICE_NOT_ENABLED": "该 Gateway 项目尚未开通向量数据库服务。",
        "GATEWAY_CONTEXT_SERVICE_NOT_ENABLED": "该 Gateway 项目尚未开通上下文服务。",
        "GATEWAY_CONTEXT_API_KEY_REQUIRED": "用户上下文服务尚未配置，请联系管理员配置服务端 Gateway Context Key。",
        "GATEWAY_CONTEXT_ACCESS_DENIED": "此 API Key 无权访问 Gateway 项目上下文。",
        "GATEWAY_CONTEXT_VERSION_CONFLICT": "数据已被其他请求更新，请刷新后重试。",
        "GATEWAY_CONTEXT_NOT_FOUND": "上下文记录不存在或已被删除。",
        "GATEWAY_CREDENTIAL_ENCRYPTION_KEY_REQUIRED": "服务端尚未配置 Gateway Key 加密密钥。",
        "GATEWAY_CREDENTIAL_ENCRYPTION_KEY_INVALID": "服务端 Gateway Key 加密配置无效。",
        "GATEWAY_CREDENTIAL_DECRYPTION_FAILED": "已保存的 Gateway Key 无法解密，请重新配置。",
        "GATEWAY_BASE_URL_NOT_CONFIGURED": "服务端尚未配置 Gateway 地址。",
        "GATEWAY_VECTOR_CONFIGURATION_REQUIRED": "服务端尚未配置 Gateway URL。",
        "GATEWAY_CONTEXT_TIMEOUT": "Gateway 请求超时，请稍后重试。",
        "GATEWAY_CONTEXT_NETWORK_ERROR": "暂时无法连接 Gateway。",
        "VECTOR_GATEWAY_TIMEOUT": "Gateway 请求超时，请稍后重试。",
        "VECTOR_GATEWAY_NETWORK_ERROR": "暂时无法连接 Gateway。",
    }
    return HTTPException(status_code=status, detail={"code": code, "message": messages.get(code, "Gateway 请求失败，请检查配置后重试。")})


