"""HTTP 请求与响应契约。"""
from typing import Any
from pydantic import BaseModel, Field, SecretStr

class AuthCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


