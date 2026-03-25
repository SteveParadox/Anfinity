"""Application configuration using Pydantic Settings."""
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


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
    
    # LLM - OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT: int = 30  # seconds
    
    # LLM - Ollama Fallback
    OLLAMA_ENABLED: bool = Field(default=True)  # Enable Ollama fallback
    OLLAMA_BASE_URL: str = Field(default="http://localhost:11434")  # Ollama server URL
    OLLAMA_MODEL: str = Field(default="phi3")  # Default Ollama model
    OLLAMA_TIMEOUT: int = 60  # Ollama can be slower
    
    # LLM Settings
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 1000
    LLM_USE_FALLBACK: bool = True  # Enable fallback on OpenAI errors
    
    # Embeddings
    EMBEDDING_PROVIDER: str = Field(default="openai")  # openai, cohere, bge
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    COHERE_API_KEY: Optional[str] = None
    COHERE_EMBEDDING_MODEL: str = "embed-english-v3.0"
    BGE_MODEL_NAME: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIMENSION: int = 1536  # Depends on model
    EMBEDDING_BATCH_SIZE: int = 100
    
    # Embedding Fallback (OpenAI -> Ollama)
    EMBEDDING_FALLBACK_ENABLED: bool = Field(default=True)  # Enable fallback to Ollama on OpenAI errors
    EMBEDDING_FALLBACK_MAX_RETRIES: int = Field(default=2)  # Max retries on OpenAI before fallback
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
    
    def __init__(self, **data):
        """Initialize settings and validate/fix OLLAMA_BASE_URL."""
        super().__init__(**data)
        
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


# Global settings instance
settings = Settings()
