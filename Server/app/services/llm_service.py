"""LLM Integration Service - OpenAI with Ollama fallback."""
import logging
import os
import time
from typing import Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum

from app.config import settings

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    """Supported LLM providers."""
    OPENAI = "openai"
    OLLAMA = "ollama"


@dataclass
class LLMResponse:
    """LLM response wrapper."""
    answer: str
    tokens_used: int
    model: str
    provider: LLMProvider


class OllamaClient:
    """Ollama client wrapper for local LLM inference."""
    
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "phi3",
        timeout: int = 60
    ):
        """Initialize Ollama client.
        
        Args:
            base_url: Ollama server URL
            model: Model name to use
            timeout: Request timeout in seconds
        """
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.client = None
        self._init_client()
    
    def _init_client(self) -> None:
        """Initialize Ollama Python client."""
        try:
            import ollama
            self.client = ollama
            logger.info(f"✓ Ollama client initialized: {self.base_url}")
        except ImportError:
            logger.error("ollama package not installed - cannot use Ollama fallback")
            self.client = None
    
    def is_available(self) -> bool:
        """Check if Ollama server is available.
        
        Returns:
            True if server responds, False otherwise
        """
        if not self.client:
            return False
        
        try:
            import requests
            response = requests.get(
                f"{self.base_url}/api/tags",
                timeout=5
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Ollama health check failed: {e}")
            return False
    
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        num_predict: int = 1000,
    ) -> Tuple[str, int]:
        """Generate response from Ollama.
        
        Args:
            prompt: User prompt
            system_prompt: System instructions
            temperature: Model temperature
            num_predict: Max tokens to generate
            
        Returns:
            Tuple of (response_text, estimated_tokens)
            
        Raises:
            RuntimeError: If Ollama is not available or request fails
        """
        if not self.client:
            raise RuntimeError("Ollama client not initialized")
        
        try:
            logger.debug(f"Calling Ollama ({self.model}) - temp={temperature}")
            
            response = self.client.generate(
                model=self.model,
                prompt=prompt if not system_prompt else f"{system_prompt}\n\n{prompt}",
                stream=False,
                options={
                    "temperature": temperature,
                    "num_predict": num_predict,
                }
            )
            
            text = response.get("response", "")
            # Estimate tokens (rough approximation: 1 token ≈ 4 chars)
            estimated_tokens = len(text) // 4
            
            logger.debug(f"Ollama generated response (~{estimated_tokens} tokens)")
            return text, estimated_tokens
        
        except Exception as e:
            logger.error(f"Ollama generation failed: {str(e)}", exc_info=True)
            raise RuntimeError(f"Ollama error: {str(e)}")


class LLMService:
    """Service for generating answers using LLM with fallback support."""
    
    # Error patterns that indicate token exhaustion
    TOKEN_EXHAUSTION_PATTERNS = [
        "rate limit",
        "quota exceeded",
        "insufficient_quota",
        "tokens_per_min_limit_exceeded",
        "400",  # Too many tokens in request
    ]
    
    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        openai_model: str = "gpt-4o-mini",
        ollama_base_url: str = "http://localhost:11434",
        ollama_model: str = "phi3",
        use_fallback: bool = True
    ):
        """Initialize LLM service with OpenAI and optional Ollama fallback.
        
        Args:
            openai_api_key: OpenAI API key
            openai_model: OpenAI model name
            ollama_base_url: Ollama server URL
            ollama_model: Ollama model name
            use_fallback: Enable fallback to Ollama on OpenAI errors
        """
        self.openai_api_key = openai_api_key or settings.OPENAI_API_KEY
        self.openai_model = openai_model
        self.use_fallback = use_fallback
        
        # Initialize OpenAI client
        self.openai_client = None
        self._init_openai()
        
        # Initialize Ollama client
        self.ollama_client = OllamaClient(
            base_url=ollama_base_url,
            model=ollama_model,
            timeout=settings.OLLAMA_TIMEOUT
        )
    
    def _init_openai(self) -> None:
        """Initialize OpenAI client."""
        if not self.openai_api_key:
            logger.warning("OpenAI API key not configured")
            self.openai_client = None
            return
        
        try:
            import openai
            self.openai_client = openai.OpenAI(api_key=self.openai_api_key)
            logger.info(f"✓ OpenAI client initialized: {self.openai_model}")
        except ImportError:
            logger.error("openai package not installed")
            self.openai_client = None
    
    def _is_token_exhaustion_error(self, error_str: str) -> bool:
        """Check if error indicates token exhaustion/rate limit.
        
        Args:
            error_str: Error message
            
        Returns:
            True if error is token exhaustion related
        """
        error_lower = error_str.lower()
        return any(pattern in error_lower for pattern in self.TOKEN_EXHAUSTION_PATTERNS)
    
    def generate_answer(
        self,
        query: str,
        context_chunks: List[str],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        force_ollama: bool = False
    ) -> LLMResponse:
        """Generate answer from query and context chunks.
        
        Tries OpenAI first, falls back to Ollama if enabled and OpenAI fails.
        
        Args:
            query: User query
            context_chunks: List of context chunks
            temperature: Model temperature (0.0-1.0)
            max_tokens: Max response tokens
            force_ollama: Force use of Ollama (skip OpenAI)
            
        Returns:
            LLM response with answer, token count, model, and provider
            
        Raises:
            RuntimeError: If all providers fail
        """
        temperature = temperature or settings.LLM_TEMPERATURE
        max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        
        # Try OpenAI first (unless forced to use Ollama)
        if not force_ollama and self.openai_client:
            try:
                return self._generate_with_openai(
                    query, context_chunks, temperature, max_tokens
                )
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"OpenAI failed: {error_msg}")
                
                # If token exhaustion, log it and fallback
                if self._is_token_exhaustion_error(error_msg):
                    logger.warning(f"⚠️  Token limit/rate limit error, attempting Ollama fallback")
                
                # If fallback is disabled, raise immediately
                if not self.use_fallback:
                    raise RuntimeError(
                        f"OpenAI error and fallback disabled: {error_msg}"
                    )
        
        # Fallback to Ollama
        if self.ollama_client and self.use_fallback:
            try:
                logger.info("Using Ollama fallback for LLM generation")
                return self._generate_with_ollama(
                    query, context_chunks, temperature, max_tokens
                )
            except Exception as e:
                logger.error(f"Ollama fallback also failed: {str(e)}")
                raise RuntimeError(
                    f"Both OpenAI and Ollama failed: {str(e)}"
                )
        
        # No providers available
        raise RuntimeError(
            "No LLM providers available (OpenAI not configured or fallback disabled)"
        )
    
    def _generate_with_openai(
        self,
        query: str,
        context_chunks: List[str],
        temperature: float,
        max_tokens: int
    ) -> LLMResponse:
        """Generate answer using OpenAI.
        
        Args:
            query: User query
            context_chunks: List of context chunks
            temperature: Model temperature
            max_tokens: Max response tokens
            
        Returns:
            LLM response
        """
        # Build context text
        context_text = "\n\n".join([
            f"[Document {i+1}]: {chunk}"
            for i, chunk in enumerate(context_chunks)
        ])
        
        # Build prompts
        system_prompt = """You are a helpful assistant that answers questions based on the provided documents.
Use only the information from the documents to answer the question.
If the documents don't contain enough information, say so clearly.
Cite the document numbers when referencing specific information.
Be concise and accurate."""
        
        user_prompt = f"""Documents:
{context_text}

Question: {query}

Answer:"""
        
        logger.debug(f"OpenAI: Calling with {len(context_chunks)} context chunks")
        
        try:
            response = self.openai_client.chat.completions.create(
                model=self.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=settings.OPENAI_TIMEOUT
            )
            
            answer_text = response.choices[0].message.content
            tokens_used = response.usage.total_tokens
            
            logger.debug(f"✓ OpenAI generated answer ({tokens_used} tokens)")
            
            return LLMResponse(
                answer=answer_text,
                tokens_used=tokens_used,
                model=self.openai_model,
                provider=LLMProvider.OPENAI
            )
        
        except Exception as e:
            error_msg = str(e)
            # Check for specific error codes
            if "429" in error_msg or "quota" in error_msg.lower():
                logger.warning(f"OpenAI rate limit/quota error: {error_msg}")
            raise
    
    def _generate_with_ollama(
        self,
        query: str,
        context_chunks: List[str],
        temperature: float,
        max_tokens: int
    ) -> LLMResponse:
        """Generate answer using Ollama.
        
        Args:
            query: User query
            context_chunks: List of context chunks
            temperature: Model temperature
            max_tokens: Max response tokens
            
        Returns:
            LLM response
        """
        # Build context text
        context_text = "\n\n".join([
            f"[Document {i+1}]: {chunk}"
            for i, chunk in enumerate(context_chunks)
        ])
        
        system_prompt = """You are a helpful assistant that answers questions based on the provided documents.
Use only the information from the documents to answer the question.
If the documents don't contain enough information, say so clearly.
Cite the document numbers when referencing specific information.
Be concise and accurate."""
        
        user_prompt = f"""Documents:
{context_text}

Question: {query}

Answer:"""
        
        logger.debug(f"Ollama: Generating with {len(context_chunks)} context chunks")
        
        answer_text, estimated_tokens = self.ollama_client.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            num_predict=max_tokens
        )
        
        logger.debug(f"✓ Ollama generated answer (~{estimated_tokens} tokens)")
        
        return LLMResponse(
            answer=answer_text,
            tokens_used=estimated_tokens,
            model=self.ollama_client.model,
            provider=LLMProvider.OLLAMA
        )
    
    def extract_citations(self, text: str) -> List[int]:
        """Extract document citations from answer.
        
        Args:
            text: Answer text
            
        Returns:
            List of cited document numbers
        """
        citations = []
        import re
        
        # Look for [Document N] pattern
        for match in re.finditer(r'\[?[Dd]ocument\s+(\d+)\]?', text):
            doc_num = int(match.group(1))
            if doc_num not in citations:
                citations.append(doc_num)
        
        return sorted(citations)
    
    def get_status(self) -> dict:
        """Get LLM service status.
        
        Returns:
            Status dict with provider availability
        """
        return {
            "openai_available": self.openai_client is not None,
            "openai_model": self.openai_model,
            "ollama_available": self.ollama_client.is_available(),
            "ollama_model": self.ollama_client.model,
            "fallback_enabled": self.use_fallback,
        }


# Singleton instance
_llm_service: Optional[LLMService] = None


def get_llm_service(
    openai_api_key: Optional[str] = None,
    openai_model: Optional[str] = None,
    model: Optional[str] = None,
    use_fallback: Optional[bool] = None
) -> LLMService:
    """Get or create LLM service singleton.
    
    Args:
        openai_api_key: OpenAI API key (optional)
        openai_model: OpenAI model name (optional)
        use_fallback: Enable fallback (optional)
        
    Returns:
        LLMService instance
    """
    global _llm_service
    
    if _llm_service is None:
        _llm_service = LLMService(
            openai_api_key=openai_api_key or settings.OPENAI_API_KEY,
            openai_model=openai_model or settings.OPENAI_MODEL,
            ollama_base_url=settings.OLLAMA_BASE_URL,
            ollama_model=settings.OLLAMA_MODEL,
            use_fallback=use_fallback if use_fallback is not None else settings.LLM_USE_FALLBACK
        )
    
    return _llm_service
