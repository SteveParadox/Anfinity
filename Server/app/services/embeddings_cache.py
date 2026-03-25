"""Redis-based embeddings cache for production efficiency.

Caches embeddings to reduce API calls and improve performance.
Features:
- Automatic cache invalidation
- Multi-provider support
- Distributed cache for multi-worker setup
- Cache statistics and monitoring
"""

import json
import logging
import hashlib
from typing import Optional, List
import pickle

import redis.asyncio as redis
from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingsCacheKey:
    """Helper class for generating cache keys."""
    
    PREFIX = "embeddings"
    
    @staticmethod
    def make_key(
        text: str,
        provider: str,
        model: str,
    ) -> str:
        """Generate a cache key for an embedding.
        
        Args:
            text: Text to embed
            provider: Embedding provider (openai, cohere, bge)
            model: Model name
        
        Returns:
            Cache key
        """
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        return f"{EmbeddingsCacheKey.PREFIX}:{provider}:{model}:{text_hash}"
    
    @staticmethod
    def make_batch_key(
        texts: List[str],
        provider: str,
        model: str,
    ) -> str:
        """Generate a cache key for a batch of embeddings.
        
        Args:
            texts: List of texts
            provider: Embedding provider
            model: Model name
        
        Returns:
            Cache key for batch
        """
        combined = "|".join(texts)
        batch_hash = hashlib.sha256(combined.encode()).hexdigest()[:16]
        return f"{EmbeddingsCacheKey.PREFIX}:batch:{provider}:{model}:{batch_hash}"


class EmbeddingsCache:
    """Redis-based cache for embeddings with async support."""
    
    def __init__(self, redis_url: str = None, ttl_hours: int = 24):
        """Initialize embeddings cache.
        
        Args:
            redis_url: Redis connection URL (defaults to settings.REDIS_URL)
            ttl_hours: Time-to-live for cached embeddings (hours)
        """
        self.redis_url = redis_url or settings.REDIS_URL
        self.ttl_seconds = ttl_hours * 3600
        self._redis = None
    
    async def connect(self):
        """Establish Redis connection."""
        if self._redis is None:
            try:
                self._redis = await redis.from_url(
                    self.redis_url,
                    encoding="utf8",
                    decode_responses=False,  # Raw bytes for binary data
                    socket_keepalive=True,
                    
                )
                logger.info("Embeddings cache connected to Redis")
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                raise
    
    async def disconnect(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None
            logger.info("Embeddings cache disconnected from Redis")
    
    async def get(
        self,
        text: str,
        provider: str,
        model: str,
    ) -> Optional[List[float]]:
        """Get cached embedding for text.
        
        Args:
            text: Text that was embedded
            provider: Embedding provider
            model: Model name
        
        Returns:
            Cached embedding as list of floats, or None if not cached
        """
        if self._redis is None:
            return None
        
        key = EmbeddingsCacheKey.make_key(text, provider, model)
        
        try:
            cached = await self._redis.get(key)
            if cached:
                # Deserialize from pickle
                embedding = pickle.loads(cached)
                logger.debug(f"Cache hit: {key[:30]}...")
                return embedding
            return None
        except Exception as e:
            logger.warning(f"Cache retrieval error: {e}")
            return None
    
    async def get_batch(
        self,
        texts: List[str],
        provider: str,
        model: str,
    ) -> Optional[List[List[float]]]:
        """Get cached embeddings for a batch of texts.
        
        Args:
            texts: List of texts
            provider: Embedding provider
            model: Model name
        
        Returns:
            List of embeddings, or None if batch not cached
        """
        if self._redis is None or not texts:
            return None
        
        key = EmbeddingsCacheKey.make_batch_key(texts, provider, model)
        
        try:
            cached = await self._redis.get(key)
            if cached:
                embeddings = pickle.loads(cached)
                logger.debug(f"Cache hit: batch of {len(texts)} items")
                return embeddings
            return None
        except Exception as e:
            logger.warning(f"Batch cache retrieval error: {e}")
            return None
    
    async def set(
        self,
        text: str,
        embedding: List[float],
        provider: str,
        model: str,
    ) -> bool:
        """Cache an embedding.
        
        Args:
            text: Original text
            embedding: Embedding vector
            provider: Embedding provider
            model: Model name
        
        Returns:
            True if cached successfully
        """
        if self._redis is None or not embedding:
            return False
        
        key = EmbeddingsCacheKey.make_key(text, provider, model)
        
        try:
            serialized = pickle.dumps(embedding)
            await self._redis.setex(
                key,
                self.ttl_seconds,
                serialized,
            )
            logger.debug(f"Cached embedding: {key[:30]}...")
            return True
        except Exception as e:
            logger.warning(f"Cache storage error: {e}")
            return False
    
    async def set_batch(
        self,
        texts: List[str],
        embeddings: List[List[float]],
        provider: str,
        model: str,
    ) -> bool:
        """Cache a batch of embeddings.
        
        Args:
            texts: List of original texts
            embeddings: List of embedding vectors
            provider: Embedding provider
            model: Model name
        
        Returns:
            True if cached successfully
        """
        if self._redis is None or not texts or len(texts) != len(embeddings):
            return False
        
        key = EmbeddingsCacheKey.make_batch_key(texts, provider, model)
        
        try:
            serialized = pickle.dumps(embeddings)
            await self._redis.setex(
                key,
                self.ttl_seconds,
                serialized,
            )
            logger.info(f"Cached batch: {len(texts)} embeddings")
            return True
        except Exception as e:
            logger.warning(f"Batch cache storage error: {e}")
            return False
    
    async def delete(
        self,
        text: str,
        provider: str,
        model: str,
    ) -> bool:
        """Delete a cached embedding.
        
        Args:
            text: Original text
            provider: Embedding provider
            model: Model name
        
        Returns:
            True if deleted successfully
        """
        if self._redis is None:
            return False
        
        key = EmbeddingsCacheKey.make_key(text, provider, model)
        
        try:
            result = await self._redis.delete(key)
            if result:
                logger.debug(f"Deleted cached embedding: {key[:30]}...")
            return bool(result)
        except Exception as e:
            logger.warning(f"Cache deletion error: {e}")
            return False
    
    async def clear_for_provider(self, provider: str) -> int:
        """Clear all cached embeddings for a specific provider.
        
        Args:
            provider: Embedding provider name
        
        Returns:
            Number of keys deleted
        """
        if self._redis is None:
            return 0
        
        try:
            pattern = f"{EmbeddingsCacheKey.PREFIX}:{provider}:*"
            keys = await self._redis.keys(pattern)
            if keys:
                deleted = await self._redis.delete(*keys)
                logger.info(f"Cleared {deleted} cached embeddings for {provider}")
                return deleted
            return 0
        except Exception as e:
            logger.warning(f"Cache clear error: {e}")
            return 0
    
    async def get_stats(self) -> dict:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        if self._redis is None:
            return {"status": "disconnected"}
        
        try:
            # Count cached embeddings
            pattern = f"{EmbeddingsCacheKey.PREFIX}:*"
            keys = await self._redis.keys(pattern)
            
            # Estimate memory usage (rough)
            info = await self._redis.info("memory")
            
            return {
                "status": "connected",
                "total_cached_embeddings": len(keys) if keys else 0,
                "redis_memory_mb": info.get("used_memory_human", "unknown"),
                "estimated_memory_bytes": info.get("used_memory", 0),
            }
        except Exception as e:
            logger.warning(f"Error getting stats: {e}")
            return {"status": "error", "error": str(e)}


# Global cache instance
_cache: Optional[EmbeddingsCache] = None


async def get_embeddings_cache() -> EmbeddingsCache:
    """Get or create the global embeddings cache instance.
    
    Returns:
        EmbeddingsCache instance
    """
    global _cache
    if _cache is None:
        _cache = EmbeddingsCache()
        await _cache.connect()
    return _cache


async def cache_embedding(
    text: str,
    embedding: List[float],
    provider: str,
    model: str,
) -> bool:
    """Cache a single embedding using the global cache.
    
    Args:
        text: Original text
        embedding: Embedding vector
        provider: Embedding provider
        model: Model name
    
    Returns:
        True if cached successfully
    """
    cache = await get_embeddings_cache()
    return await cache.set(text, embedding, provider, model)


async def cache_batch_embeddings(
    texts: List[str],
    embeddings: List[List[float]],
    provider: str,
    model: str,
) -> bool:
    """Cache a batch of embeddings using the global cache.
    
    Args:
        texts: List of original texts
        embeddings: List of embedding vectors
        provider: Embedding provider
        model: Model name
    
    Returns:
        True if cached successfully
    """
    cache = await get_embeddings_cache()
    return await cache.set_batch(texts, embeddings, provider, model)


async def get_cached_embedding(
    text: str,
    provider: str,
    model: str,
) -> Optional[List[float]]:
    """Get a cached embedding from global cache.
    
    Args:
        text: Original text
        provider: Embedding provider
        model: Model name
    
    Returns:
        Cached embedding, or None if not found
    """
    cache = await get_embeddings_cache()
    return await cache.get(text, provider, model)


async def get_cached_embeddings(
    texts: List[str],
    provider: str,
    model: str,
) -> Optional[List[List[float]]]:
    """Get cached embeddings for multiple texts.
    
    Args:
        texts: List of original texts
        provider: Embedding provider
        model: Model name
    
    Returns:
        Cached embeddings, or None if not found
    """
    cache = await get_embeddings_cache()
    return await cache.get_batch(texts, provider, model)
