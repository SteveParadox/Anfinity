"""Content type detection and classification utilities."""
import re
from urllib.parse import urlparse
from typing import Optional


def detect_content_type(content: str) -> str:
    """Auto-detect content type from string.
    
    Returns:
        Type: 'url', 'email', 'code', 'structured', 'text'
    """
    content = content.strip()
    
    # Check for URL
    url_pattern = r'^https?://|^www\.|^ftp://'
    if re.match(url_pattern, content):
        return "url"
    
    # Check for email format
    if "@" in content and len(content.split('\n')) == 1:
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if re.match(email_pattern, content):
            return "email"
    
    # Check for JSON
    if content.startswith(('{', '[')):
        try:
            import json
            json.loads(content)
            return "structured"
        except:
            pass
    
    # Check for CSV
    lines = content.split('\n')
    if len(lines) > 1 and ',' in lines[0]:
        comma_count = sum(line.count(',') for line in lines[:min(3, len(lines))])
        if comma_count > 0:
            return "structured"
    
    # Check for code (common indicators)
    code_indicators = [
        'def ', 'class ', 'import ', 'function ', 'const ', 'let ',
        'async ', 'await ', 'return ', 'if __name__', '<?php', '<%'
    ]
    if any(indicator in content for indicator in code_indicators):
        return "code"
    
    # Default to plain text
    return "text"


def extract_entities(text: str) -> dict:
    """Extract named entities from text.
    
    Returns:
        Dictionary with people, organizations, locations, dates, concepts
    """
    try:
        import spacy
        nlp = spacy.load("en_core_web_sm")
        doc = nlp(text)
        
        entities = {
            "people": [],
            "organizations": [],
            "locations": [],
            "dates": [],
            "concepts": []
        }
        
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                entities["people"].append(ent.text)
            elif ent.label_ in ("ORG", "PRODUCT"):
                entities["organizations"].append(ent.text)
            elif ent.label_ in ("GPE", "LOC"):
                entities["locations"].append(ent.text)
            elif ent.label_ in ("DATE", "TIME"):
                entities["dates"].append(ent.text)
        
        # Remove duplicates
        for key in entities:
            entities[key] = list(set(entities[key]))
        
        return entities
    except ImportError:
        # spaCy not installed, return empty
        return {
            "people": [],
            "organizations": [],
            "locations": [],
            "dates": [],
            "concepts": []
        }


def classify_topics(text: str) -> list:
    """Classify topics in text.
    
    Returns:
        List of topic tags
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.cluster import KMeans
        
        # Basic topic extraction via TF-IDF
        words = text.lower().split()
        # Filter common words
        stop_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at',
            'to', 'for', 'of', 'that', 'this', 'is', 'are', 'was'
        }
        keywords = [w for w in words if w not in stop_words and len(w) > 3]
        
        # Return top 5 frequencies
        from collections import Counter
        top_topics = Counter(keywords).most_common(5)
        return [topic[0] for topic in top_topics]
    except ImportError:
        return []


def classify_sentiment(text: str) -> str:
    """Classify sentiment of text.
    
    Returns:
        'positive', 'negative', or 'neutral'
    """
    try:
        from textblob import TextBlob
        blob = TextBlob(text)
        polarity = blob.sentiment.polarity
        
        if polarity > 0.1:
            return "positive"
        elif polarity < -0.1:
            return "negative"
        else:
            return "neutral"
    except ImportError:
        return "neutral"
