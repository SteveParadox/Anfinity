"""Base document parser."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional
import re


@dataclass
class ParsedDocument:
    """Parsed document result."""
    text: str
    metadata: Dict[str, Any]
    title: Optional[str] = None
    author: Optional[str] = None
    page_count: Optional[int] = None
    word_count: Optional[int] = None


class DocumentParser(ABC):
    """Abstract base class for document parsers."""
    
    @abstractmethod
    def parse(self, file_bytes: bytes) -> ParsedDocument:
        """Parse document bytes.
        
        Args:
            file_bytes: Raw file content
            
        Returns:
            Parsed document
        """
        pass
    
    def _clean_text(self, text: str) -> str:
        """Clean and normalize text.
        
        Args:
            text: Raw text
            
        Returns:
            Cleaned text
        """
        # Preserve line structure because downstream chunking relies on headings,
        # paragraphs, and page markers remaining intact.
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Remove control characters except newlines and tabs.
        text = "".join(
            char for char in text
            if char in ("\n", "\t") or ord(char) >= 32
        )

        # Normalize intra-line whitespace without collapsing line breaks.
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)

        # Keep at most one blank line between content blocks.
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()
    
    def _extract_title(self, text: str) -> Optional[str]:
        """Extract title from text.
        
        Args:
            text: Document text
            
        Returns:
            Extracted title or None
        """
        lines = text.strip().split('\n')
        if lines:
            first_line = lines[0].strip()
            if len(first_line) < 200 and not first_line.startswith('#'):
                return first_line
        return None
    
    def _count_words(self, text: str) -> int:
        """Count words in text.
        
        Args:
            text: Document text
            
        Returns:
            Word count
        """
        return len(text.split())
