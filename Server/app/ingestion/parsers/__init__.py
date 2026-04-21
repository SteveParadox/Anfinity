"""Document parsers for various file types."""
from app.ingestion.parsers.base import DocumentParser, ParsedDocument
from app.ingestion.parsers.pdf import PDFParser
from app.ingestion.parsers.word import WordParser
from app.ingestion.parsers.text import TextParser
from app.ingestion.parsers.html import HTMLParser
from app.ingestion.parsers.url import URLParser
from app.ingestion.parsers.code import CodeParser
from app.ingestion.parsers.data import DataParser

__all__ = [
    "DocumentParser",
    "ParsedDocument", 
    "PDFParser",
    "WordParser",
    "TextParser",
    "HTMLParser",
    "URLParser",
    "CodeParser",
    "DataParser",
    "get_parser",
    "detect_content_type",
]


def get_parser(content_type: str) -> DocumentParser:
    """Get appropriate parser for content type.
    
    Args:
        content_type: MIME type of document
        
    Returns:
        Document parser instance
    """
    parsers = {
        "application/pdf": PDFParser(),
        "text/plain": TextParser(),
        "text/markdown": TextParser(),
        "text/x-markdown": TextParser(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": WordParser(),
        "application/msword": WordParser(),
        "text/html": HTMLParser(),
        "application/xhtml+xml": HTMLParser(),
        "text/url": URLParser(),
        "application/url": URLParser(),
        "text/code": CodeParser(),
        "application/code": CodeParser(),
        "application/json": DataParser(),
        "text/json": DataParser(),
        "text/csv": DataParser(),
        "application/csv": DataParser(),
    }
    
    parser = parsers.get(content_type)
    if not parser:
        # Default to text parser
        parser = TextParser()
    
    return parser


def detect_content_type(filename: str, file_bytes: bytes = None) -> str:
    """Detect content type from filename and optionally content.
    
    Args:
        filename: Name of file
        file_bytes: Optional file content for deeper detection
        
    Returns:
        MIME type string
    """
    filename_lower = filename.lower()
    
    # URL detection
    if filename_lower.startswith("http://") or filename_lower.startswith("https://"):
        return "text/url"
    
    # Code file detection
    code_extensions = {
        ".py": "text/code", ".js": "text/code", ".ts": "text/code",
        ".jsx": "text/code", ".tsx": "text/code", ".java": "text/code",
        ".cpp": "text/code", ".c": "text/code", ".cs": "text/code",
        ".go": "text/code", ".rs": "text/code", ".rb": "text/code",
        ".php": "text/code", ".swift": "text/code", ".kt": "text/code",
        ".scala": "text/code", ".r": "text/code", ".sql": "text/code",
        ".sh": "text/code", ".bash": "text/code",
    }
    
    for ext, mime_type in code_extensions.items():
        if filename_lower.endswith(ext):
            return mime_type
    
    # Data file detection
    if filename_lower.endswith(".json"):
        return "application/json"
    if filename_lower.endswith(".csv"):
        return "text/csv"
    
    # Standard document detection
    if filename_lower.endswith(".pdf"):
        return "application/pdf"
    if filename_lower.endswith((".docx", ".doc")):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if filename_lower.endswith((".html", ".htm", ".xhtml")):
        return "text/html"
    if filename_lower.endswith((".md", ".markdown")):
        return "text/markdown"
    
    # If content provided, try deeper detection
    if file_bytes:
        try:
            content = file_bytes.decode("utf-8", errors="ignore").strip()
            
            # Check for JSON
            if (content.startswith("{") or content.startswith("[")) and content.endswith(("}","]")):
                try:
                    import json
                    json.loads(content)
                    return "application/json"
                except:
                    pass
            
            # Check for CSV (simple heuristic)
            if "," in content and "\n" in content:
                return "text/csv"
        except:
            pass
    
    # Default to text
    return "text/plain"
