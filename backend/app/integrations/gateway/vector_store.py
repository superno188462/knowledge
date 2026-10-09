"""兼容旧导入路径；实现已迁至独立 gateway_api 仓库。"""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("gateway_client.vector_store")
