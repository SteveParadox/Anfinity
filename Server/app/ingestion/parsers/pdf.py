"""PDF document parser using PyMuPDF."""
import io
from typing import Dict, Any

import fitz  # PyMuPDF

from app.ingestion.parsers.base import DocumentParser, ParsedDocument


class PDFParser(DocumentParser):
    """PDF document parser."""
    
    def parse(self, file_bytes: bytes) -> ParsedDocument:
        """Parse PDF document.
        
        Args:
            file_bytes: PDF file content
            
        Returns:
            Parsed document
        """
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        
        text_parts = []
        metadata = {}
        
        # Extract PDF metadata
        pdf_metadata = doc.metadata
        if pdf_metadata:
            metadata['title'] = pdf_metadata.get('title')
            metadata['author'] = pdf_metadata.get('author')
            metadata['subject'] = pdf_metadata.get('subject')
            metadata['creator'] = pdf_metadata.get('creator')
            metadata['creation_date'] = pdf_metadata.get('creationDate')
            metadata['modification_date'] = pdf_metadata.get('modDate')
        
        # Get page count before processing (needed before closing document)
        page_count = len(doc)
        pages_with_text = 0
        pages_without_text = 0
        total_extracted_chars = 0

        # Extract text from each page
        for page_num in range(page_count):
            page = doc[page_num]
            
            # Use explicit text extraction mode so metrics are more predictable.
            text = page.get_text("text")
            page_text_length = len(text.strip())
            total_extracted_chars += page_text_length
            
            # Add page marker
            if page_text_length:
                pages_with_text += 1
                text_parts.append(f"\n--- Page {page_num + 1} ---\n")
                text_parts.append(text)
            else:
                pages_without_text += 1
            
            # Extract page-level metadata
            page_info = {
                'page_number': page_num + 1,
                'width': page.rect.width,
                'height': page.rect.height,
                'text_length': page_text_length,
            }
            
            if 'pages' not in metadata:
                metadata['pages'] = []
            metadata['pages'].append(page_info)

        metadata['pages_with_text'] = pages_with_text
        metadata['pages_without_text'] = pages_without_text
        metadata['total_extracted_chars'] = total_extracted_chars
        metadata['extraction_method'] = 'pymupdf_text'
        
        doc.close()
        
        # Combine text
        full_text = '\n'.join(text_parts)
        
        # Clean text
        full_text = self._clean_text(full_text)
        
        # Extract title if not in metadata
        title = metadata.get('title') or self._extract_title(full_text)
        
        # Count words
        word_count = self._count_words(full_text)
        
        return ParsedDocument(
            text=full_text,
            metadata=metadata,
            title=title,
            author=metadata.get('author'),
            page_count=page_count,
            word_count=word_count
        )
