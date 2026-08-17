"""Hybrid embeddings cache: in-memory + Redis for distributed caching.

Provides:
- Fast in-memory L1 cache
- Distributed Redis L2 cache for multi-worker setups
- Cache hit/miss statistics
- Configurable TTL and size limits
"""

import json
import logging
import hashlib
import threading
from typing import List, Optional, Dict, Tuple
from datetime import datetime
from collections import OrderedDict

import redis
from app.config import settings

logger = logging.getLogger(__name__)


class HybridEmbeddingsCache:
    """Two-tier cache: in-memory L1 + Redis L2.
    
    This provides fast lookups via in-memory cache while allowing
    cross-worker sharing via Redis for distributed setups.
    """
    
    def __init__(
        self,
        redis_url: str = None,
        l1_max_size: int = 1000,
        enable_l2: bool = True,
    ):
        """Initialize hybrid cache.
        
        Args:
            redis_url: Redis connection URL (defaults to settings.REDIS_URL)
            l1_max_size: Maximum items in L1 memory cache
            enable_l2: Whether to use L2 Redis cache
        """
        self.redis_url = redis_url or settings.REDIS_URL
        self.l1_max_size = l1_max_size
        self.enable_l2 = enable_l2
        
        # L1 cache: in-memory with LRU eviction
        self.l1_cache: OrderedDict = OrderedDict()
        self._lock = threading.RLock()
        
        # L2 cache: Redis connection
        self._redis_conn = None
        if enable_l2:
            try:
                self._redis_conn = redis.from_url(
                    self.redis_url,
                    decode_responses=False,  # Binary pickle data
                    socket_keepalive=True,
                    
                )
                # Test connection
                self._redis_conn.ping()
                logger.info("Hybrid embeddings cache initialized with L1 + L2")
            except Exception as e:
                logger.warning(f"Redis L2 cache unavailable: {e}")
                self._redis_conn = None
                self.enable_l2 = False
        
        # Statistics
        self.stats = {
            "l1_hits": 0,
            "l2_hits": 0,
            "misses": 0,
        }
    
    def _compute_key(self, text: str, model: str) -> str:
        """Compute cache key for text."""
        content = f"{model}:{text}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def _redis_key(self, text_hash: str) -> str:
        """Get Redis key for hash."""
        return f"emb:{text_hash}"
    
    def get(self, text: str, model: str) -> Optional[List[float]]:
        """Get cached embedding with automatic promotion from L2 to L1.
        
        Args:
            text: Original text
            model: Model name
        
        Returns:
            Cached embedding or None
        """
        text_hash = self._compute_key(text, model)
        
        with self._lock:
            # Try L1 cache
            if text_hash in self.l1_cache:
                self.l1_cache.move_to_end(text_hash)  # Mark as recently used
                self.stats["l1_hits"] += 1
                logger.debug(f"L1 cache hit: {text_hash[:12]}...")
                return self.l1_cache[text_hash]
        
        # Try L2 cache (Redis)
        if self.enable_l2 and self._redis_conn:
            try:
                key = self._redis_key(text_hash)
                cached = self._redis_conn.get(key)
                if cached:
                    # Deserialize and promote to L1
                    import pickle
                    embedding = pickle.loads(cached)
                    
                    with self._lock:
                        self.l1_cache[text_hash] = embedding
                        self.l1_cache.move_to_end(text_hash)
                        if len(self.l1_cache) > self.l1_max_size:
                            self.l1_cache.popitem(last=False)  # Remove oldest
                    
                    self.stats["l2_hits"] += 1
                    logger.debug(f"L2 cache hit: {text_hash[:12]}... (promoted to L1)")
                    return embedding
            except Exception as e:
                logger.warning(f"L2 cache retrieval error: {e}")
        
        self.stats["misses"] += 1
        return None
    
    def set(
        self,
        text: str,
        model: str,
        embedding: List[float],
        l2_ttl_seconds: int = 86400,
    ) -> bool:
        """Cache embedding in both L1 and L2.
        
        Args:
            text: Original text
            model: Model name
            embedding: Embedding vector
            l2_ttl_seconds: TTL in Redis (default 24 hours)
        
        Returns:
            True if cached in L1, may differ for L2
        """
        if not embedding:
            return False
        
        text_hash = self._compute_key(text, model)
        
        # Store in L1
        with self._lock:
            self.l1_cache[text_hash] = embedding
            self.l1_cache.move_to_end(text_hash)
            if len(self.l1_cache) > self.l1_max_size:
                self.l1_cache.popitem(last=False)  # Remove oldest
        
        # Store in L2 (async, don't block)
        if self.enable_l2 and self._redis_conn:
            try:
                import pickle
                key = self._redis_key(text_hash)
                self._redis_conn.setex(
                    key,
                    l2_ttl_seconds,
                    pickle.dumps(embedding),
                )
            except Exception as e:
                logger.warning(f"L2 cache storage error: {e}")
        
        return True
    
    def get_batch(
        self,
        texts: List[str],
        model: str,
    ) -> Tuple[List[str], Dict[str, List[float]]]:
        """Get cached embeddings for batch.
        
        Args:
            texts: List of texts to look up
            model: Model name
        
        Returns:
            Tuple of (missing_texts, cached_map)
        """
        missing = []
        cached_map = {}
        
        for text in texts:
            embedding = self.get(text, model)
            if embedding is not None:
                cached_map[text] = embedding
            else:
                missing.append(text)
        
        return missing, cached_map
    
    def set_batch(
        self,
        texts: List[str],
        model: str,
        embeddings: List[List[float]],
        l2_ttl_seconds: int = 86400,
    ) -> None:
        """Cache batch of embeddings.
        
        Args:
            texts: List of original texts
            model: Model name
            embeddings: List of embedding vectors
            l2_ttl_seconds: TTL in Redis
        """
        for text, embedding in zip(texts, embeddings):
            self.set(text, model, embedding, l2_ttl_seconds)
    
    def clear(self) -> None:
        """Clear all caches."""
        with self._lock:
            self.l1_cache.clear()
        
        if self.enable_l2 and self._redis_conn:
            try:
                # Delete all embeddings cache keys
                for key in self._redis_conn.scan_iter("emb:*"):
                    self._redis_conn.delete(key)
                logger.info("Cleared all embeddings caches")
            except Exception as e:
                logger.warning(f"Error clearing L2 cache: {e}")
    
    def get_stats(self) -> dict:
        """Get cache statistics.
        
        Returns:
            Dictionary with hit rates and sizes
        """
        with self._lock:
            total_requests = (
                self.stats["l1_hits"] +
                self.stats["l2_hits"] +
                self.stats["misses"]
            )
            
            return {
                "l1_size": len(self.l1_cache),
                "l1_hits": self.stats["l1_hits"],
                "l2_hits": self.stats["l2_hits"],
                "misses": self.stats["misses"],
                "total_requests": total_requests,
                "l1_hit_rate": (
                    self.stats["l1_hits"] / total_requests * 100
                    if total_requests > 0 else 0
                ),
                "l2_hit_rate": (
                    self.stats["l2_hits"] / total_requests * 100
                    if total_requests > 0 else 0
                ),
                "overall_hit_rate": (
                    (self.stats["l1_hits"] + self.stats["l2_hits"]) /
                    total_requests * 100
                    if total_requests > 0 else 0
                ),
            }
