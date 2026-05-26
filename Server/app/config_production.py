"""Production environment configuration template."""
from typing import Optional
import os


class ProductionSettings:
    """Production-specific settings for deployment."""
    
    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://user:password@db-instance:5432/anfinity"
    )
    DATABASE_POOL_SIZE: int = 30  # Increased for production
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_SSL_MODE: str = "require"  # Enforce SSL in production
    
    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis-cluster:6379/0")
    REDIS_SENTINEL_HOSTS: Optional[list] = None  # For HA
    
    # Qdrant Vector DB
    QDRANT_URL: str = os.getenv("QDRANT_URL", "http://qdrant:6333")
    QDRANT_API_KEY: Optional[str] = os.getenv("QDRANT_API_KEY")
    QDRANT_SNAPSHOTS_PATH: str = "/var/lib/qdrant/snapshots"
    
    # S3 Storage
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    S3_ENDPOINT_URL: Optional[str] = os.getenv("S3_ENDPOINT_URL")  # None for AWS
    S3_BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME", "anfinity-prod")
    S3_REGION: str = os.getenv("S3_REGION", "us-east-1")
    S3_CREATE_BUCKET: bool = os.getenv("S3_CREATE_BUCKET", "false").lower() in {"1", "true", "yes", "on"}
    S3_ENABLE_VERSIONING: bool = True
    S3_STORAGE_CLASS: str = "STANDARD_IA"  # Cost optimization
    
    # Security
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")  # MUST be set
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")  # MUST be set
    
    # CORS - Strict production origins
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "").split(",")
    
    # Embeddings
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    COHERE_API_KEY: Optional[str] = os.getenv("COHERE_API_KEY")
    
    # Celery worker configuration
    CELERY_TASK_TIME_LIMIT: int = 3600  # 1 hour
    CELERY_TASK_SOFT_TIME_LIMIT: int = 3300
    CELERY_TASK_DEFAULT_RETRY_DELAY: int = 60
    CELERY_RETRY_BASE_SECONDS: int = 60
    CELERY_RETRY_MAX_SECONDS: int = 900
    CELERY_RESULT_EXPIRES: int = 86400
    CELERY_WORKER_POOL: str = os.getenv("CELERY_WORKER_POOL", "prefork")
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = int(os.getenv("CELERY_WORKER_PREFETCH_MULTIPLIER", "1"))
    CELERY_WORKER_MAX_TASKS_PER_CHILD: int = int(os.getenv("CELERY_WORKER_MAX_TASKS_PER_CHILD", "100"))
    CELERY_BROKER_VISIBILITY_TIMEOUT: int = int(os.getenv("CELERY_BROKER_VISIBILITY_TIMEOUT", "7200"))
    CELERY_BROKER_SOCKET_TIMEOUT: int = int(os.getenv("CELERY_BROKER_SOCKET_TIMEOUT", "30"))
    CELERY_BROKER_SOCKET_CONNECT_TIMEOUT: int = int(os.getenv("CELERY_BROKER_SOCKET_CONNECT_TIMEOUT", "30"))
    CELERY_TASK_RETRY_POLICY: dict = {
        'max_retries': 3,
        'interval_start': 1,
        'interval_step': 1,
        'interval_max': 10,
    }
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"  # Structured logging for production
    
    # Monitoring
    SENTRY_DSN: Optional[str] = os.getenv("SENTRY_DSN")
    PROMETHEUS_ENABLED: bool = True
    
    @classmethod
    def validate(cls) -> dict[str, bool]:
        """Validate all required production settings are configured."""
        required = {
            'JWT_SECRET': bool(cls.JWT_SECRET),
            'ENCRYPTION_KEY': bool(cls.ENCRYPTION_KEY),
            'AWS_ACCESS_KEY_ID': bool(cls.AWS_ACCESS_KEY_ID),
            'OPENAI_API_KEY': bool(cls.OPENAI_API_KEY),
            'DATABASE_URL': bool(cls.DATABASE_URL),
        }
        
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(f"Missing required production settings: {', '.join(missing)}")
        
        return required
