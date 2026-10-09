"""Typed settings: secrets from the environment / .env, tunables from config/*.yaml.

Everything else reads configuration through get_settings() and get_config() only.
"""

from __future__ import annotations

import os
from datetime import tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"


class Settings(BaseSettings):
    """Secrets and connection strings from the environment or ``.env``."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore", env_ignore_empty=True)

    jobpilot_env: Literal["dev", "test", "prod"] = "dev"
    # Long-lived processes (laptop worker + dispatcher, migrations): a session connection, because the queue
    # and dispatcher use LISTEN/NOTIFY and advisory locks. On Supabase: the direct or *session* pooler URL.
    database_url: str = "postgresql+psycopg://localhost/jobpilot"
    # Serverless dashboard (Vercel): Supabase transaction pooler URL (port 6543). Falls back to database_url.
    database_pooler_url: str | None = None
    vercel: bool = False  # Vercel sets VERCEL=1

    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_storage_bucket: str = "jobpilot"

    qdrant_url: str | None = None
    qdrant_api_key: SecretStr | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: int | None = None

    mcp_telegram_dispatcher_key: SecretStr | None = None
    mcp_telegram_worker_key: SecretStr | None = None
    mcp_gmail_dispatcher_key: SecretStr | None = None
    mcp_gmail_worker_key: SecretStr | None = None
    mcp_web_worker_key: SecretStr | None = None

    approval_token_key: SecretStr | None = None
    approval_signing_key: SecretStr | None = None  # Option B: Ed25519 private key, dispatcher only
    approval_verify_key: str | None = None  # Option B: Ed25519 public key, Gmail MCP server

    gmail_oauth_client_id: str | None = None
    gmail_oauth_client_secret: SecretStr | None = None
    gmail_token_key: SecretStr | None = None  # Fernet key encrypting the stored refresh token

    jsearch_api_key: SecretStr | None = None
    jsearch_host: str = "jsearch.p.rapidapi.com"
    serpapi_api_key: SecretStr | None = None  # fallback jobs API (Google Jobs via SerpApi)
    openai_api_key: SecretStr | None = None

    dashboard_totp_secret: SecretStr | None = None
    dashboard_session_secret: SecretStr | None = None
    dashboard_base_url: str = "http://127.0.0.1:8765"

    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    @property
    def psycopg_dsn(self) -> str:
        """The same database URL in the plain form psycopg and LangGraph expect."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @property
    def serverless(self) -> bool:
        """True when running on Vercel (no long-lived processes)."""
        return self.vercel

    @property
    def telegram_dry_run(self) -> bool:
        """True with no bot token: Telegram calls are only logged."""
        return self.telegram_bot_token is None or not self.telegram_bot_token.get_secret_value()


class Caps(BaseModel):
    """Daily caps on cards, emails and jobs-API calls."""

    cards_per_day: int
    emails_per_day: int | None  # None: no daily email limit (decision D4)
    jobs_api_calls_per_day: int


class Budgets(BaseModel):
    """Token budgets for runs and Job Chat, and the cost alert factor."""

    tokens_per_day: int | None  # None: no daily token limit
    run_tokens_estimate: int
    cost_alert_multiplier: float
    job_chat_tokens_per_message: int
    job_chat_tokens_per_day: int = 120_000


class QueueCfg(BaseModel):
    """How long queued jobs live and how fast their score decays."""

    expiry_days: int
    age_decay_per_day: float


class Price(BaseModel):
    """Per-token prices of one model, used for cost tracking."""

    input: float
    output: float
    cache_read: float
    cache_write: float


class LLMCfg(BaseModel):
    """Limits applied to every LLM call, and the reasoning effort of reasoning models (gpt-5)."""

    max_tokens: int = Field(ge=1, le=128000)
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None
    reasoning_effort_by_task: dict[str, Literal["minimal", "low", "medium", "high"]] = Field(
        default_factory=dict
    )
    reasoning_headroom_tokens: int = Field(default=0, ge=0, le=64000)

    def effort_for(self, task: str, model: str) -> str | None:
        """The reasoning effort for a task, or None for a model that doesn't reason (gpt-4o-mini)."""
        if not model.startswith(("gpt-5", "o1", "o3", "o4")):
            return None
        return self.reasoning_effort_by_task.get(task, self.reasoning_effort)


class LeaseCfg(BaseModel):
    """Lease lifetimes and heartbeat interval for runs and work items."""

    run_ttl_seconds: int
    heartbeat_seconds: int
    work_item_ttl_seconds: int


class WorkerCfg(BaseModel):
    """Worker queue concurrency, retry count and poll interval."""

    concurrency: int
    work_item_max_attempts: int
    poll_seconds: float


class TelegramCfg(BaseModel):
    """Long-polling timeout and the dry-run log path."""

    long_poll_timeout_seconds: int
    dry_run_log: str


class DispatcherCfg(BaseModel):
    """Outbox delivery polling and retry backoff settings."""

    poll_seconds: float
    max_attempts: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    email_first_retry_seconds: float = 60


class TracingCfg(BaseModel):
    """Tracing output options and the trace URL pattern."""

    console: bool
    trace_url: str = "{host}/trace/{trace_id}"


class AlertsCfg(BaseModel):
    """Thresholds that raise operator alerts on Telegram."""

    budget_fraction: float = 0.8
    quota_fraction: float = 0.8
    integration_failures: int = 3
    stuck_minutes: int = 10


class PrivacyCfg(BaseModel):
    """Data retention settings for event payloads."""

    event_payload_retention_days: int = 180


class LoginCfg(BaseModel):
    """Dashboard login lockout and session lifetime settings."""

    max_failures: int = 5
    window_minutes: int = 15
    session_hours: int = 12


class ResumeCfg(BaseModel):
    """Resume template, compile sandbox, page limit and review loop."""

    template: str
    sandbox: Literal["auto", "macos", "docker", "container"]
    compile_timeout_seconds: int
    max_pages: int
    storage_dir: str
    max_bullet_chars: int = 210
    known_words: list[str] = Field(default_factory=list)  # words the resume spell check never changes
    review_pass_score: int = 85
    review_max_rounds: int = 2


class EmbeddingsCfg(BaseModel):
    """The local embedding model, its vector size and cache folder."""

    model: str
    dim: int
    cache_dir: str


class VectorsCfg(BaseModel):
    """Where the embedded (local) Qdrant stores its data."""

    local_path: str
    timeout_seconds: int = 30  # Qdrant Cloud requests (the client's default is 5 s)


class AppConfig(BaseModel):
    """Tunables from ``config/defaults.yaml`` and ``mcp_scopes.yaml``."""

    shadow_mode: bool
    shadow_creates_gmail_drafts: bool = False
    timezone: str = "Asia/Kolkata"
    match_threshold: int
    caps: Caps
    budgets: Budgets
    queue: QueueCfg
    agents: dict[str, dict[str, int]]
    reminders: dict[str, int]
    outreach: dict[str, int]
    alerts: AlertsCfg = Field(default_factory=AlertsCfg)
    privacy: PrivacyCfg = Field(default_factory=PrivacyCfg)
    login: LoginCfg = Field(default_factory=LoginCfg)
    models: dict[str, str]
    prompt_versions: dict[str, int]
    pricing: dict[str, Price]
    llm: LLMCfg
    lease: LeaseCfg
    worker: WorkerCfg
    telegram: TelegramCfg
    dispatcher: DispatcherCfg
    tracing: TracingCfg
    resume: ResumeCfg
    embeddings: EmbeddingsCfg
    vectors: VectorsCfg
    mcp_scopes: dict[str, dict[str, list[str]]] = Field(default_factory=dict)

    def model_for(self, task: str) -> str:
        """Return the pinned model id for a task.

        Args:
            task: The task name, e.g. ``extract`` or ``tailor``.

        Returns:
            The task's model id, or the default model when none is
                pinned.
        """
        return self.models.get(task, self.models["default"])

    @property
    def tz(self) -> tzinfo:
        """The user's local time zone."""
        return ZoneInfo(self.timezone)


def load_yaml(name: str) -> dict:
    """Load one YAML file from the ``config/`` folder.

    Args:
        name: The file name inside ``config/``.

    Returns:
        The parsed mapping (empty when the file is empty).
    """
    with (CONFIG_DIR / name).open() as f:
        return yaml.safe_load(f) or {}


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached after the first call)."""
    if os.environ.get("JOBPILOT_ENV") == "test":
        # Tests must not pick up the developer's real .env file.
        return Settings(_env_file=None)  # type: ignore[call-arg]  # BaseSettings runtime override
    return Settings()


@lru_cache
def get_config() -> AppConfig:
    """Return the process-wide app config (cached after the first call)."""
    data = load_yaml("defaults.yaml")
    data["mcp_scopes"] = load_yaml("mcp_scopes.yaml")
    return AppConfig.model_validate(data)
