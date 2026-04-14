"""Token encryption utilities for secure storage of OAuth credentials."""
import os
import logging
from cryptography.fernet import Fernet

from app.config import settings

logger = logging.getLogger(__name__)
_DEV_ENCRYPTION_KEY = Fernet.generate_key().decode()


class TokenEncryptor:
    """Encrypts and decrypts sensitive tokens."""
    
    def __init__(self):
        """Initialize encryptor with key from environment or generate."""
        key = settings.ENCRYPTION_KEY or os.environ.get("ENCRYPTION_KEY")
        
        if not key:
            if settings.ENVIRONMENT == "production":
                raise ValueError(
                    "ENCRYPTION_KEY must be set in production. "
                    "Generate with: python -c 'from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())'"
                )
            key = _DEV_ENCRYPTION_KEY
            logger.warning("ENCRYPTION_KEY not set; using a temporary development encryption key")
        
        if isinstance(key, str):
            key = key.encode()
        
        self.cipher = Fernet(key)
    
    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string.
        
        Args:
            plaintext: String to encrypt
            
        Returns:
            Encrypted string (base64 encoded)
        """
        if not plaintext:
            return ""
        
        encrypted = self.cipher.encrypt(plaintext.encode())
        return encrypted.decode()
    
    def decrypt(self, ciphertext: str) -> str:
        """Decrypt an encrypted string.
        
        Args:
            ciphertext: Encrypted string to decrypt
            
        Returns:
            Decrypted plaintext
        """
        if not ciphertext:
            return ""
        
        try:
            decrypted = self.cipher.decrypt(ciphertext.encode())
            return decrypted.decode()
        except Exception as e:
            raise ValueError(f"Failed to decrypt token: {e}")


# Global encryptor instance
token_encryptor = TokenEncryptor()
