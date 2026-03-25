"""Token encryption utilities for secure storage of OAuth credentials."""
import os
from cryptography.fernet import Fernet
from typing import Optional

from app.config import settings


class TokenEncryptor:
    """Encrypts and decrypts sensitive tokens."""
    
    def __init__(self):
        """Initialize encryptor with key from environment or generate."""
        key = os.environ.get("ENCRYPTION_KEY")
        
        if not key:
            if settings.ENVIRONMENT == "production":
                raise ValueError(
                    "ENCRYPTION_KEY must be set in production. "
                    "Generate with: python -c 'from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())'"
                )
            # Generate a test key for development
            key = Fernet.generate_key().decode()
        
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
