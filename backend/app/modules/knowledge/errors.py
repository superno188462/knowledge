"""向量错误到业务 HTTP 响应的转换。"""
from fastapi import HTTPException
from app.integrations.gateway.errors import GatewayVectorStoreError

def vector_gateway_error(error: GatewayVectorStoreError) -> HTTPException:
    status = {
        "VECTOR_PARENT_NOT_FOUND": 404,
        "VECTOR_PARENT_IN_USE": 409,
        "VECTOR_VERSION_IMMUTABLE": 409,
        "VECTOR_VERSION_RETIRED": 409,
        "VECTOR_PUBLICATION_HISTORY_UNKNOWN": 409,
        "VECTOR_STAGED_VERSION_NOT_FOUND": 404,
        "VECTOR_REQUEST_TOO_LARGE": 413,
        "VECTOR_PUBLICATION_PENDING": 503,
        "VECTOR_DOCUMENT_TARGET_MISMATCH": 409,
        "VECTOR_STORE_NOT_CONFIGURED": 503,
        "VECTOR_DIMENSIONS_CONFIG_INVALID": 503,
        "VECTOR_COLLECTION_MODEL_MISMATCH": 409,
        "VECTOR_COLLECTION_DIMENSIONS_MISMATCH": 409,
        "VECTOR_COLLECTION_CHUNKING_MISMATCH": 409,
        "VECTOR_COLLECTION_CHUNK_SIZE_MISMATCH": 409,
        "VECTOR_GATEWAY_AUTH_FAILED": 502,
        "VECTOR_GATEWAY_SERVICE_NOT_ENABLED": 403,
        "VECTOR_GATEWAY_TIMEOUT": 504,
        "VECTOR_COLLECTION_NOT_FOUND": 404,
        "VECTOR_GATEWAY_DOCUMENT_STATE_CONFLICT": 409,
        "VECTOR_USER_QUOTA_EXCEEDED": 429,
        "VECTOR_PROJECT_QUOTA_EXCEEDED": 429,
        "VECTOR_EMBEDDING_QUOTA_EXCEEDED": 429,
        "VECTOR_QUOTA_CONFIGURATION_ERROR": 503,
        "VECTOR_RECORD_TOO_LARGE": 413,
        "VECTOR_RECORD_EMPTY": 400,
        "VECTOR_USER_QUOTA_MISSING": 403,
        "VECTOR_EMBEDDING_UPSTREAM_FAILED": 502,
        "VECTOR_EMBEDDING_INPUT_INVALID": 422,
        "VECTOR_EMBEDDING_DIMENSIONS_UNSUPPORTED": 422,
        "VECTOR_EMBEDDING_DIMENSIONS_INVALID": 502,
        "VECTOR_EMBEDDING_RESPONSE_INVALID": 502,
        "VECTOR_GATEWAY_INVALID_RESPONSE": 502,
        "VECTOR_GATEWAY_INCOMPLETE_UPSERT": 502,
        "VECTOR_GATEWAY_INVALID_REQUEST": 500,
        "VECTOR_DUPLICATE_RECORD_ID": 500,
    }.get(error.code, 502)
    messages = {
        "VECTOR_PARENT_NOT_FOUND": "父块记录不存在，请重新嵌入该文档。",
        "VECTOR_PARENT_IN_USE": "父块仍被子片引用，需先删除相关子片。",
        "VECTOR_VERSION_IMMUTABLE": "已发布版本不能修改，请保存新版本后再嵌入。",
        "VECTOR_VERSION_RETIRED": "该版本已经退役，请保存新版本后再嵌入。",
        "VECTOR_PUBLICATION_HISTORY_UNKNOWN": "Gateway 旧版本发布历史不明，请使用新版本重新嵌入。",
        "VECTOR_STAGED_VERSION_NOT_FOUND": "暂存版本不存在，请重新提交嵌入。",
        "VECTOR_REQUEST_TOO_LARGE": "记录批次超过 Gateway 请求大小限制。",
        "VECTOR_PUBLICATION_PENDING": "版本发布结果未确认，请重试原任务。新记录已保留，请勿更换目标或修改文档。",
        "VECTOR_DOCUMENT_TARGET_MISMATCH": "此文档的活动向量属于其他网关或集合，请切换回对应集合后删除。",
        "VECTOR_STORE_NOT_CONFIGURED": "服务端尚未配置 Gateway URL 和 collection。",
        "VECTOR_COLLECTION_MODEL_MISMATCH": "已有集合绑定了不同的 Embedding 模型。",
        "VECTOR_COLLECTION_CHUNK_SIZE_MISMATCH": "已有集合的文本长度上限与配置不一致。",
        "VECTOR_GATEWAY_AUTH_FAILED": "Gateway 项目鉴权失败。",
        "VECTOR_GATEWAY_SERVICE_NOT_ENABLED": "Gateway 项目尚未开通向量服务。",
        "VECTOR_GATEWAY_TIMEOUT": "Gateway 请求超时，请稍后重试。",
        "VECTOR_GATEWAY_NETWORK_ERROR": "暂时无法连接 Gateway。",
        "VECTOR_COLLECTION_NOT_FOUND": "集合不存在，请先初始化。",
        "VECTOR_GATEWAY_DOCUMENT_STATE_CONFLICT": "Gateway 中该切片的文档状态已变化，可能有重复嵌入请求。请刷新后再重试。",
        "VECTOR_USER_QUOTA_EXCEEDED": "当前 Gateway 用户 Embedding 额度不足。原有向量版本仍保留。",
        "VECTOR_PROJECT_QUOTA_EXCEEDED": "当前 Gateway 项目 Embedding 额度不足。原有向量版本仍保留。",
        "VECTOR_EMBEDDING_QUOTA_EXCEEDED": "Embedding 额度不足。原有向量版本仍保留。",
        "VECTOR_QUOTA_CONFIGURATION_ERROR": "Gateway Embedding 服务额度配置异常。",
        "VECTOR_RECORD_TOO_LARGE": "至少有一个切片超过 Gateway 集合的字符上限，请减小切片长度后重试。原有向量记录仍保留。",
        "VECTOR_RECORD_EMPTY": "存在空切片，请检查文档内容或切片规则。",
        "VECTOR_USER_QUOTA_MISSING": "Gateway 项目所有者尚未配置 Embedding 用户额度。",
        "VECTOR_EMBEDDING_UPSTREAM_FAILED": "Gateway 无法完成 Embedding 上游调用，请检查模型路由和模型服务状态。",
        "VECTOR_EMBEDDING_INPUT_INVALID": "Gateway 拒绝了 Embedding 输入格式。",
        "VECTOR_EMBEDDING_DIMENSIONS_UNSUPPORTED": "当前 Embedding 模型不支持集合请求的向量维度配置。",
        "VECTOR_EMBEDDING_DIMENSIONS_INVALID": "Gateway 返回的向量维度无效。",
        "VECTOR_EMBEDDING_RESPONSE_INVALID": "Gateway 返回的 Embedding 响应格式无效。",
        "VECTOR_GATEWAY_INVALID_RESPONSE": "Gateway 返回的向量响应格式无效，文档尚未标记为嵌入成功。",
        "VECTOR_GATEWAY_INCOMPLETE_UPSERT": "Gateway 未确认全部切片写入，文档尚未标记为嵌入成功。",
        "VECTOR_GATEWAY_INVALID_REQUEST": "本项目构造的切片请求无效，请检查后端日志。",
        "VECTOR_DUPLICATE_RECORD_ID": "同一批切片存在重复 ID，请检查文档切片配置。",
    }
    detail: dict[str, Any] = {
        "code": error.code,
        "message": messages.get(error.code, "向量服务请求失败。"),
    }
    if error.status_code is not None:
        detail["gateway_status"] = error.status_code
    if error.gateway_trace_id:
        detail["gateway_trace_id"] = error.gateway_trace_id
    if error.upstream_code:
        # Expose only the Gateway's stable machine code, never its response body.
        detail["gateway_code"] = error.upstream_code
        if error.code == "VECTOR_GATEWAY_ERROR":
            detail["message"] = f"Gateway 向量请求失败（错误码：{error.upstream_code}）。"
    elif error.code == "VECTOR_GATEWAY_ERROR" and error.status_code is not None:
        detail["message"] = f"Gateway 向量请求返回 HTTP {error.status_code}。"
        if error.gateway_trace_id:
            detail["message"] += f" Trace ID：{error.gateway_trace_id}。"
    return HTTPException(status_code=status, detail=detail)


