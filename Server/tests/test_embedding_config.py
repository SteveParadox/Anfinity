import pytest

from app import config


class DummySettings:
    LLM_PROVIDER = "ollama"
    EMBEDDING_PROVIDER = "openai"
    EMBEDDING_BASE_URL = "https://example.com/v1"
    EMBEDDING_API_KEY = ""
    EMBEDDING_MODEL = ""
    OPENAI_BASE_URL = None
    OPENAI_API_KEY = None
    OPENAI_LLM_MODEL = None
    OPENAI_MODEL = "gpt-4o-mini"
    OLLAMA_BASE_URL = "http://localhost:11434"
    OLLAMA_MODEL = "phi3:mini"
    OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
    OLLAMA_ENABLED = True
    OLLAMA_API_KEY = None
    OLLAMA_TIMEOUT = 150
    OLLAMA_CONNECT_TIMEOUT = 10
    OLLAMA_READ_TIMEOUT = 150
    OLLAMA_WRITE_TIMEOUT = 30
    OLLAMA_POOL_TIMEOUT = 30
    OLLAMA_EMBED_TIMEOUT = 150
    OLLAMA_EMBED_BATCH_SIZE = 32
    OLLAMA_MAX_CONCURRENT_REQUESTS = 2
    EMBEDDING_DIMENSION = 768
    EMBEDDING_BATCH_SIZE = 32
    EMBEDDING_FALLBACK_ENABLED = True
    EMBEDDING_FALLBACK_MAX_RETRIES = 2
    COHERE_API_KEY = None
    COHERE_EMBEDDING_MODEL = "embed-english-v3.0"
    BGE_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def test_embedding_base_url_requires_key_and_model():
    with pytest.raises(ValueError, match="EMBEDDING_BASE_URL.*EMBEDDING_API_KEY.*EMBEDDING_MODEL"):
        config.build_ai_runtime_config(DummySettings())


def test_embedding_base_url_uses_explicit_embeddings_config():
    source = DummySettings()
    source.EMBEDDING_API_KEY = "test-api-key"
    source.EMBEDDING_MODEL = "text-embedding-3-small"

    runtime = config.build_ai_runtime_config(source)

    assert runtime.openai.base_url == "https://example.com/v1"
    assert runtime.openai.api_key == "test-api-key"
    assert runtime.openai.embedding_model == "text-embedding-3-small"
