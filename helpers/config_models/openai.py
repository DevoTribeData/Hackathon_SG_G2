from pydantic import SecretStr
from pydantic_settings import BaseSettings
from typing import Optional


class OpenAiModel(BaseSettings, env_prefix="openai_"):
    api_key: Optional[SecretStr] = None
    endpoint: str
    gpt_deployment: str
    gpt_model: str