"""Application configuration using Pydantic Settings."""
import secrets
from dataclasses import dataclass
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_DEFAULT_OLLAMA_LLM_MODEL = "gpt-oss:20b-cloud"
_DEFAULT_OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


@dataclass(frozen=True)
class OllamaRuntimeConfig:
    enabled: bool
    base_url: str
    api_key: Optional[str]
    llm_model: str
    embedding_model: str
    timeout: int
    embedding_timeout: int
    embedding_batch_size: int
    max_concurrent_requests: int


@dataclass(frozen=True)
class OpenAIRuntimeConfig:
    api_key: Optional[str]
    llm_model: str
    embedding_model: str
    timeout: int


@dataclass(frozen=True)
class EmbeddingRuntimeConfig:
    provider: str
    dimension: int
    batch_size: int
    fallback_enabled: bool
    fallback_max_retries: int
    cohere_api_key: Optional[str]
    cohere_model: str
    bge_model: str


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: str
    use_fallback: bool
    temperature: float
    max_tokens: int
    openai_model: str
    ollama_model: str


@dataclass(frozen=True)
class AIRuntimeConfig:
    ollama: OllamaRuntimeConfig
    openai: OpenAIRuntimeConfig
    embeddings: EmbeddingRuntimeConfig
    llm: LLMRuntimeConfig


def _normalize_provider(value: Optional[str], *, allowed: set[str], default: str) -> str:
    provider = (value or default).strip().lower()
    return provider if provider in allowed else default


def _normalize_ollama_base_url(value: Optional[str]) -> str:
    base_url = (value or _DEFAULT_OLLAMA_BASE_URL).strip().rstrip("/")
    if not base_url or base_url == "https://ollama.com":
        return _DEFAULT_OLLAMA_BASE_URL
    return base_url


def _normalize_secret(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    secret = str(value).strip()
    if not secret:
        return None

    lowered = secret.lower()
    placeholder_markers = (
        "...",
        "your-",
        "placeholder",
        "example",
        "set your",
        "changeme",
        "replace-me",
    )
    if any(marker in lowered for marker in placeholder_markers):
        return None
    return secret


def _build_ollama_headers(api_key: Optional[str], *, include_content_type: bool = True) -> dict[str, str]:
    headers: dict[str, str] = {}
    if include_content_type:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def build_ai_runtime_config(source: object) -> AIRuntimeConfig:
    """Build a normalized AI runtime view from a settings-like object."""
    llm_provider = _normalize_provider(
        getattr(source, "LLM_PROVIDER", "ollama"),
        allowed={"ollama", "openai"},
        default="ollama",
    )
    embedding_provider = _normalize_provider(
        getattr(source, "EMBEDDING_PROVIDER", "ollama"),
        allowed={"ollama", "openai", "cohere", "bge"},
        default="ollama",
    )
    ollama_base_url = _normalize_ollama_base_url(getattr(source, "OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE_URL))
    ollama_llm_model = getattr(source, "OLLAMA_MODEL", _DEFAULT_OLLAMA_LLM_MODEL) or _DEFAULT_OLLAMA_LLM_MODEL
    ollama_embedding_model = (
        getattr(source, "OLLAMA_EMBEDDING_MODEL", _DEFAULT_OLLAMA_EMBEDDING_MODEL)
        or _DEFAULT_OLLAMA_EMBEDDING_MODEL
    )
    openai_llm_model = getattr(source, "OPENAI_MODEL", _DEFAULT_OPENAI_MODEL) or _DEFAULT_OPENAI_MODEL
    openai_embedding_model = (
        getattr(source, "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        or "text-embedding-3-small"
    )

    ollama = OllamaRuntimeConfig(
        enabled=bool(getattr(source, "OLLAMA_ENABLED", True)),
        base_url=ollama_base_url,
        api_key=_normalize_secret(getattr(source, "OLLAMA_API_KEY", None)),
        llm_model=ollama_llm_model,
        embedding_model=ollama_embedding_model,
        timeout=int(getattr(source, "OLLAMA_TIMEOUT", 150) or 150),
        embedding_timeout=int(getattr(source, "OLLAMA_EMBED_TIMEOUT", getattr(source, "OLLAMA_TIMEOUT", 150)) or 150),
        embedding_batch_size=int(
            getattr(source, "OLLAMA_EMBED_BATCH_SIZE", getattr(source, "EMBEDDING_BATCH_SIZE", 32)) or 32
        ),
        max_concurrent_requests=int(getattr(source, "OLLAMA_MAX_CONCURRENT_REQUESTS", 2) or 2),
    )
    openai = OpenAIRuntimeConfig(
        api_key=_normalize_secret(getattr(source, "OPENAI_API_KEY", None)),
        llm_model=openai_llm_model,
        embedding_model=openai_embedding_model,
        timeout=int(getattr(source, "OPENAI_TIMEOUT", 30) or 30),
    )
    embeddings = EmbeddingRuntimeConfig(
        provider=embedding_provider,
        dimension=int(getattr(source, "EMBEDDING_DIMENSION", 768) or 768),
        batch_size=int(getattr(source, "EMBEDDING_BATCH_SIZE", 32) or 32),
        fallback_enabled=bool(getattr(source, "EMBEDDING_FALLBACK_ENABLED", True)),
        fallback_max_retries=int(getattr(source, "EMBEDDING_FALLBACK_MAX_RETRIES", 2) or 2),
        cohere_api_key=_normalize_secret(getattr(source, "COHERE_API_KEY", None)),
        cohere_model=getattr(source, "COHERE_EMBEDDING_MODEL", "embed-english-v3.0") or "embed-english-v3.0",
        bge_model=getattr(source, "BGE_MODEL_NAME", "BAAI/bge-small-en-v1.5") or "BAAI/bge-small-en-v1.5",
    )
    llm = LLMRuntimeConfig(
        provider=llm_provider,
        use_fallback=bool(getattr(source, "LLM_USE_FALLBACK", True)),
        temperature=float(getattr(source, "LLM_TEMPERATURE", 0.3) or 0.3),
        max_tokens=int(getattr(source, "LLM_MAX_TOKENS", 1000) or 1000),
        openai_model=openai_llm_model,
        ollama_model=ollama_llm_model,
    )
    return AIRuntimeConfig(
        ollama=ollama,
        openai=openai,
        embeddings=embeddings,
        llm=llm,
    )


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Application
    APP_NAME: str = "CogniFlow API"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = Field(default=True)
    
    # Security
    JWT_SECRET: str = Field(default="")  # MUST be set in production via env
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    JWT_ISSUER: str = "anfinity-api"
    JWT_AUDIENCE: str = "anfinity-clients"
    ENCRYPTION_KEY: Optional[str] = None
    
    # CORS - configure for production
    CORS_ORIGINS: list = Field(default=["http://localhost:3000", "http://localhost:5173"])
    CORS_CREDENTIALS: bool = True
    CORS_METHODS: list = Field(default=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    CORS_HEADERS: list = Field(default=["*"])
    
    # Database
    DATABASE_URL: str = Field(default="postgresql://postgres:postgres@localhost:5432/cogniflow")
    DATABASE_POOL_SIZE: int = 30  # Increased from 20 for concurrent load testing
    DATABASE_MAX_OVERFLOW: int = 20  # Increased from 10 for concurrent load testing
    
    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    
    # Qdrant Vector DB
    QDRANT_URL: str = Field(default="http://localhost:6333")
    QDRANT_API_KEY: Optional[str] = None
    QDRANT_COLLECTION_PREFIX: str = "cogniflow"
    QDRANT_REQUIRED: bool = Field(default=True)  # Fail-fast if Qdrant unavailable (set to False for development without Qdrant)
    
    # S3 Storage
    AWS_ACCESS_KEY_ID: str = Field(default="minioadmin")
    AWS_SECRET_ACCESS_KEY: str = Field(default="minioadmin")
    S3_ENDPOINT_URL: Optional[str] = Field(default="http://localhost:9000")
    S3_BUCKET_NAME: str = Field(default="cogniflow")
    S3_REGION: str = Field(default="us-east-1")
    
    # LLM - Provider Selection (Primary)
    # FIX: Changed default from "openai" to "ollama" (more reliable locally, avoids quota conflicts)
    LLM_PROVIDER: str = Field(default="ollama")  # ollama (primary), openai (fallback)
    
    # LLM - OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT: int = 30  # seconds
    
    # LLM - Ollama (Primary)
    OLLAMA_ENABLED: bool = Field(default=True)  # Enable Ollama
    OLLAMA_BASE_URL: str = Field(default="http://localhost:11434")  # Ollama server URL
    OLLAMA_API_KEY: Optional[str] = None
    OLLAMA_MODEL: str = Field(default=_DEFAULT_OLLAMA_LLM_MODEL)
    OLLAMA_TIMEOUT: int = 150  # 120-180s for phi3 with context (was 60s, causing timeouts)
    OLLAMA_EMBED_TIMEOUT: int = 150
    OLLAMA_EMBED_BATCH_SIZE: int = 48
    OLLAMA_MAX_CONCURRENT_REQUESTS: int = 2
    
    # LLM Settings
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 1000
    LLM_USE_FALLBACK: bool = True  # Enable fallback on provider errors
    GRAPH_CLUSTER_SYNC_TOKEN: Optional[str] = None
    
    # Embeddings
    # FIX: Changed default from "openai" to "ollama" (more reliable locally, avoids quota conflicts)
    EMBEDDING_PROVIDER: str = Field(default="ollama")  # ollama (primary), openai/cohere/bge (fallback)
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    COHERE_API_KEY: Optional[str] = None
    COHERE_EMBEDDING_MODEL: str = "embed-english-v3.0"
    BGE_MODEL_NAME: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIMENSION: int = 768  # Ollama default: nomic-embed-text is 768D
    EMBEDDING_BATCH_SIZE: int = 32  # Optimal for nomic-embed-text via Ollama (balances GPU util. & memory)
    
    # Embedding Fallback (Ollama -> OpenAI)
    EMBEDDING_FALLBACK_ENABLED: bool = Field(default=True)  # Enable fallback to OpenAI if Ollama fails
    EMBEDDING_FALLBACK_MAX_RETRIES: int = Field(default=2)  # Max retries before fallback
    OLLAMA_EMBEDDING_MODEL: str = Field(default="nomic-embed-text")  # Ollama embedding model
    
    # Chunking
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 100
    CHUNK_MAX_TOKENS: int = 800
    
    # Connectors
    SLACK_CLIENT_ID: Optional[str] = None
    SLACK_CLIENT_SECRET: Optional[str] = None
    NOTION_CLIENT_ID: Optional[str] = None
    NOTION_CLIENT_SECRET: Optional[str] = None
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GITHUB_CLIENT_ID: Optional[str] = None
    GITHUB_CLIENT_SECRET: Optional[str] = None
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    
    # File Upload
    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_FILE_TYPES: list = Field(default=[
        "application/pdf",
        "text/plain",
        "text/markdown",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ])
    
    class Config:
        env_file = ".env"
        case_sensitive = True

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _normalize_debug(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "prod", "production", "off", "no", "false", "0"}:
                return False
            if normalized in {"dev", "debug", "development", "on", "yes", "true", "1"}:
                return True
        return value
    
    def __init__(self, **data):
        """Initialize settings and validate/fix OLLAMA_BASE_URL."""
        super().__init__(**data)

        if not self.JWT_SECRET:
            if self.ENVIRONMENT == "production":
                raise ValueError("JWT_SECRET must be set in production")
            self.JWT_SECRET = secrets.token_urlsafe(64)
        
        # Fix common misconfiguration: OLLAMA_BASE_URL pointing to public website
        if self.OLLAMA_BASE_URL == "https://ollama.com":
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "⚠️  CRITICAL CONFIG ERROR: OLLAMA_BASE_URL is set to public website 'https://ollama.com' "
                "instead of local server. Fixing to 'http://localhost:11434'."
            )
            self.OLLAMA_BASE_URL = "http://localhost:11434"
            logger.info(
                "✓ Fixed OLLAMA_BASE_URL to 'http://localhost:11434'. "
                "Please update your .env or environment variable to make this permanent."
            )

    @property
    def ai_runtime(self) -> AIRuntimeConfig:
        """Normalized AI runtime config used by provider-backed services."""
        return build_ai_runtime_config(self)


# Global settings instance
settings = Settings()


def get_ai_runtime_config() -> AIRuntimeConfig:
    """Return the centralized AI runtime configuration for the current process."""
    return settings.ai_runtime


def build_ollama_request_headers(
    source: object,
    *,
    include_content_type: bool = True,
) -> dict[str, str]:
    """Build normalized Ollama HTTP headers from a settings-like object."""
    runtime = build_ai_runtime_config(source)
    return _build_ollama_headers(runtime.ollama.api_key, include_content_type=include_content_type)


def get_ollama_request_headers(*, include_content_type: bool = True) -> dict[str, str]:
    """Return centralized Ollama headers for the current process."""
    return build_ollama_request_headers(settings, include_content_type=include_content_type)
