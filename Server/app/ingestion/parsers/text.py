"""Plain text and Markdown parser."""
import re
from typing import Dict, Any

from app.ingestion.parsers.base import DocumentParser, ParsedDocument


class TextParser(DocumentParser):
    """Plain text and Markdown parser."""
    
    def parse(self, file_bytes: bytes) -> ParsedDocument:
        """Parse text document.
        
        Args:
            file_bytes: Text file content
            
        Returns:
            Parsed document
        """
        # Detect encoding
        encodings = ['utf-8', 'latin-1', 'cp1252']
        text = None
        
        for encoding in encodings:
            try:
                text = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        
        if text is None:
            text = file_bytes.decode('utf-8', errors='ignore')
        
        # Clean text
        text = self._clean_text(text)
        
        # Extract metadata from Markdown frontmatter
        metadata = {}
        if text.startswith('---'):
            frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
            if frontmatter_match:
                frontmatter_text = frontmatter_match.group(1)
                text = text[frontmatter_match.end():]
                
                # Parse simple key: value pairs
                for line in frontmatter_text.split('\n'):
                    if ':' in line:
                        key, value = line.split(':', 1)
                        metadata[key.strip()] = value.strip()
        
        # Extract title
        title = metadata.get('title')
        if not title:
            # Try Markdown heading
            heading_match = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
            if heading_match:
                title = heading_match.group(1).strip()
            else:
                title = self._extract_title(text)
        
        # Count words
        word_count = self._count_words(text)
        
        return ParsedDocument(
            text=text,
            metadata=metadata,
            title=title,
            author=metadata.get('author'),
            page_count=None,
            word_count=word_count
        )
