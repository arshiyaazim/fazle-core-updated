"""
Fazle Core — Configuration
Reads from .env file. All settings in one place.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379/9"

    # Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"

    # Bridges
    bridge1_url: str = "http://localhost:8080"
    bridge1_number: str = "8801958122300"
    bridge1_label: str = "HR"

    bridge2_url: str = "http://localhost:8081"
    bridge2_number: str = "8801880446111"
    bridge2_label: str = "OPS"

    # Meta WhatsApp
    meta_phone_number_id: str = ""
    meta_api_token: str = ""
    meta_api_url: str = "https://graph.facebook.com/v22.0"
    meta_app_secret: str = ""
    meta_verify_token: str = "fazle_core_webhook_2026"

    # Media
    media_processor_url: str = "http://localhost:8090"

    # App
    app_port: int = 8200
    log_level: str = "INFO"
    debug: bool = False
    internal_api_key: str = "fazle-core-internal-2026"

    # Safe mode — no outgoing messages when False
    auto_reply_enabled: bool = False

    # Batch 11 — per-intent auto-reply allow-list.
    # When auto_reply_enabled=False, recruitment messages from non-admin
    # senders (job-trigger keyword OR active intake session) still auto-reply.
    # Everything else (escort, payment, attendance) stays draft-only.
    recruitment_autoreply_enabled: bool = True

    # Company
    company_name: str = "Al-Aqsa Security Service"
    accountant_phone: str = ""
    admin_numbers: str = ""
    admin_meta_number: str = "8801880446111"    # Verified Meta admin
    admin_bridge1_number: str = "8801958122300" # Bridge1 HR admin
    admin_bridge2_number: str = "8801880446111" # Bridge2 OPS admin

    @property
    def admin_number_list(self) -> list[str]:
        return [n.strip() for n in self.admin_numbers.split(",") if n.strip()]

    class Config:
        env_file = "/home/azim/fazle-core/.env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
