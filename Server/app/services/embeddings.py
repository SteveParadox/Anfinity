"""Embedding service for generating and managing embeddings."""
import os
from typing import List, Tuple
import logging

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import cohere
    HAS_COHERE = True
except ImportError:
    HAS_COHERE = False


class EmbeddingService:
    """Service for generating text embeddings."""
    
    def __init__(self, provider: str = "openai"):
        """Initialize embedding service.
        
        Args:
            provider: "openai", "cohere", or "local"
        """
        self.provider = provider
        self.model = None
        self.dimension = 1536
        
        if provider == "openai" and HAS_OPENAI:
            self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model = "text-embedding-3-small"
            self.dimension = 1536
            
        elif provider == "cohere" and HAS_COHERE:
            self.client = cohere.Client(api_key=os.getenv("COHERE_API_KEY"))
            self.model = "embed-english-v3.0"
            self.dimension = 1024
            
        else:
            # Fallback: simple mock embeddings for development
            logger.warning(f"Embedding provider '{provider}' not available. Using mock embeddings.")
            self.client = None
            self.dimension = 1536
    
    def embed_text(self, text: str) -> List[float]:
        """Embed a single text.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
        """
        return self.embed_batch([text])[0]
    
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts in batch.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            return []
        
        # Clean texts
        texts = [text.strip() for text in texts if text and text.strip()]
        
        if not texts:
            return []
        
        try:
            if self.provider == "openai" and self.client:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=texts
                )
                # Sort by index to maintain order
                embeddings = sorted(response.data, key=lambda x: x.index)
                return [e.embedding for e in embeddings]
            
            elif self.provider == "cohere" and self.client:
                response = self.client.embed(
                    texts=texts,
                    model=self.model,
                    input_type="search_document"
                )
                return response.embeddings
            
            else:
                # Mock embeddings for development
                logger.debug(f"Generating mock embeddings for {len(texts)} texts")
                return [[0.1] * self.dimension for _ in texts]
        
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}")
            # Fallback to mock embeddings
            return [[0.1] * self.dimension for _ in texts]
    
    def get_dimension(self) -> int:
        """Get embedding dimension.
        
        Returns:
            Dimension of embedding vectors
        """
        return self.dimension


# Singleton instance
_embedding_service = None


def get_embedding_service(provider: str = None) -> EmbeddingService:
    """Get or create embedding service.
    
    Args:
        provider: Embedding provider
        
    Returns:
        EmbeddingService instance
    """
    global _embedding_service
    
    if _embedding_service is None:
        provider = provider or os.getenv("EMBEDDING_PROVIDER", "openai")
        _embedding_service = EmbeddingService(provider)
    
    return _embedding_service
