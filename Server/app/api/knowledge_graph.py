"""Knowledge Graph API routes."""
from typing import List, Dict, Any, Set, Tuple
from uuid import UUID
from collections import defaultdict
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.session import get_db
from app.database.models import Document, DocumentStatus, Chunk, User as DBUser
from app.core.auth import get_current_active_user, get_workspace_context, WorkspaceContext

router = APIRouter(prefix="/knowledge-graph", tags=["Knowledge Graph"])


# Schemas
class GraphNode(BaseModel):
    """Knowledge graph node."""
    id: str
    type: str  # document, concept, tag
    label: str
    value: int = 1  # Size/importance
    metadata: Dict[str, Any] = {}


class GraphEdge(BaseModel):
    """Knowledge graph edge."""
    source: str
    target: str
    type: str  # contains, mentions, relates_to
    weight: float = 1.0


class KnowledgeGraphResponse(BaseModel):
    """Knowledge graph response."""
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    stats: Dict[str, int]


def extract_entities(text: str) -> List[str]:
    """Extract key entities/concepts from text.
    
    Simple MVP implementation using:
    - Capitalized phrases (potential proper nouns)
    - Quoted phrases
    - Technical terms
    
    Args:
        text: Input text
        
    Returns:
        List of extracted entities
    """
    entities = []
    
    # Extract capitalized phrases (2-4 words)
    capitalized_pattern = r'\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,3}\b'
    entities.extend(re.findall(capitalized_pattern, text))
    
    # Extract quoted phrases
    quoted_pattern = r'"([^"]{3,50})"'
    entities.extend(re.findall(quoted_pattern, text))
    
    # Extract technical terms (camelCase, snake_case)
    technical_pattern = r'\b[a-z]+_[a-z_]+\b|\b[a-z]+[A-Z][a-zA-Z]+\b'
    entities.extend(re.findall(technical_pattern, text))
    
    # Clean and deduplicate
    cleaned = []
    seen = set()
    
    for entity in entities:
        # Clean entity
        entity = entity.strip()
        entity = re.sub(r'\s+', ' ', entity)
        
        # Skip short entities
        if len(entity) < 3:
            continue
        
        # Skip common words
        common_words = {'The', 'This', 'That', 'These', 'Those', 'And', 'But', 'For'}
        if entity.split()[0] in common_words:
            continue
        
        # Normalize for deduplication
        key = entity.lower()
        if key not in seen and len(cleaned) < 50:  # Limit entities per document
            seen.add(key)
            cleaned.append(entity)
    
    return cleaned


def extract_tags_from_text(text: str) -> List[str]:
    """Extract potential tags from text.
    
    Args:
        text: Input text
        
    Returns:
        List of tags
    """
    tags = []
    
    # Look for hashtags
    hashtag_pattern = r'#(\w+)'
    tags.extend(re.findall(hashtag_pattern, text))
    
    # Look for keywords in brackets
    bracket_pattern = r'\[([^\]]{2,20})\]'
    tags.extend(re.findall(bracket_pattern, text))
    
    return list(set(tags))


@router.get("/{workspace_id}", response_model=KnowledgeGraphResponse)
async def get_knowledge_graph(
    workspace_id: UUID,
    limit: int = 100,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Get knowledge graph for workspace.
    
    Args:
        workspace_id: Workspace UUID
        limit: Maximum documents to include
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Knowledge graph with nodes and edges
    """
    # Verify workspace membership
    await get_workspace_context(workspace_id, current_user, db)
    
    try:
        # Get documents with chunks - more lenient query
        result = await db.execute(
            select(Document, Chunk)
            .join(Chunk, Document.id == Chunk.document_id, isouter=True)
            .where(Document.workspace_id == workspace_id)
            .order_by(Document.created_at.desc())
            .limit(limit * 5)  # Get enough chunks
        )
        
        rows = result.all()
    except Exception as e:
        # Fallback: get documents without chunks
        print(f"Error querying documents with chunks: {e}")
        result = await db.execute(
            select(Document)
            .where(Document.workspace_id == workspace_id)
            .order_by(Document.created_at.desc())
            .limit(limit)
        )
        documents = result.scalars().all()
        rows = [(doc, None) for doc in documents]
    
    if not rows or all(chunk is None for _, chunk in rows):
        return KnowledgeGraphResponse(
            nodes=[],
            edges=[],
            stats={"documents": 0, "chunks": 0, "concepts": 0, "connections": 0}
        )
    
    # Build graph
    nodes: Dict[str, GraphNode] = {}
    edges: List[GraphEdge] = []
    
    # Track document-chunk relationships
    document_chunks: Dict[str, List[str]] = defaultdict(list)
    
    # Track concept occurrences
    concept_docs: Dict[str, Set[str]] = defaultdict(set)
    
    # Process documents and chunks
    documents_seen: Set[str] = set()
    chunks_seen: Set[str] = set()
    
    for document, chunk in rows:
        doc_id = str(document.id)
        
        # Add document node (once)
        if doc_id not in documents_seen:
            documents_seen.add(doc_id)
            nodes[doc_id] = GraphNode(
                id=doc_id,
                type="document",
                label=document.title[:50],
                value=5,
                metadata={
                    "title": document.title,
                    "source_type": str(document.source_type.value) if hasattr(document.source_type, 'value') else str(document.source_type),
                    "created_at": document.created_at.isoformat() if document.created_at else None
                }
            )
        
        # Skip if no chunk
        if chunk is None:
            continue
        
        chunk_id = str(chunk.id)
        
        # Add chunk node
        if chunk_id not in chunks_seen:
            chunks_seen.add(chunk_id)
            nodes[chunk_id] = GraphNode(
                id=chunk_id,
                type="chunk",
                label=f"Chunk {chunk.chunk_index}",
                value=2,
                metadata={
                    "chunk_index": chunk.chunk_index,
                    "token_count": chunk.token_count,
                    "preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text
                }
            )
            
            # Edge: document -> chunk
            edges.append(GraphEdge(
                source=doc_id,
                target=chunk_id,
                type="contains",
                weight=1.0
            ))
            
            document_chunks[doc_id].append(chunk_id)
        
        # Extract entities from chunk
        entities = extract_entities(chunk.text)
        
        for entity in entities:
            entity_id = f"concept:{entity.lower().replace(' ', '_')}"
            
            # Add concept node
            if entity_id not in nodes:
                nodes[entity_id] = GraphNode(
                    id=entity_id,
                    type="concept",
                    label=entity,
                    value=1,
                    metadata={"mentions": 0}
                )
            
            # Increment mention count
            nodes[entity_id].value += 1
            nodes[entity_id].metadata["mentions"] = nodes[entity_id].value
            
            # Track which documents mention this concept
            concept_docs[entity_id].add(doc_id)
            
            # Edge: chunk -> concept
            edges.append(GraphEdge(
                source=chunk_id,
                target=entity_id,
                type="mentions",
                weight=0.5
            ))
    
    # Add concept-to-concept edges based on co-occurrence
    concept_list = list(concept_docs.keys())
    for i, concept1 in enumerate(concept_list):
        for concept2 in concept_list[i+1:]:
            # Calculate co-occurrence
            common_docs = concept_docs[concept1] & concept_docs[concept2]
            if len(common_docs) >= 2:  # At least 2 common documents
                edges.append(GraphEdge(
                    source=concept1,
                    target=concept2,
                    type="relates_to",
                    weight=len(common_docs) * 0.3
                ))
    
    # Add document-to-document edges based on shared concepts
    doc_list = list(documents_seen)
    for i, doc1 in enumerate(doc_list):
        for doc2 in doc_list[i+1:]:
            # Count shared concepts
            shared = 0
            for concept_id, docs in concept_docs.items():
                if doc1 in docs and doc2 in docs:
                    shared += 1
            
            if shared >= 3:  # At least 3 shared concepts
                edges.append(GraphEdge(
                    source=doc1,
                    target=doc2,
                    type="similar_to",
                    weight=min(shared * 0.2, 1.0)
                ))
    
    # Calculate stats
    concept_count = sum(1 for n in nodes.values() if n.type == "concept")
    
    stats = {
        "documents": len(documents_seen),
        "chunks": len(chunks_seen),
        "concepts": concept_count,
        "connections": len(edges)
    }
    
    return KnowledgeGraphResponse(
        nodes=list(nodes.values()),
        edges=edges,
        stats=stats
    )


@router.get("/{workspace_id}/concepts")
async def get_concepts(
    workspace_id: UUID,
    query: str = "",
    limit: int = 20,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Get concepts/entities for workspace.
    
    Args:
        workspace_id: Workspace UUID
        query: Optional search filter
        limit: Maximum results
        current_user: Authenticated user
        db: Database session
        
    Returns:
        List of concepts
    """
    # Verify workspace membership
    await get_workspace_context(workspace_id, current_user, db)
    
    # Get all chunks
    result = await db.execute(
        select(Chunk.text)
        .join(Document, Chunk.document_id == Document.id)
        .where(
            Document.workspace_id == workspace_id,
            Document.status == "indexed"
        )
        .limit(500)
    )
    
    texts = [row[0] for row in result.all()]
    
    # Extract all entities
    all_entities = []
    for text in texts:
        all_entities.extend(extract_entities(text))
    
    # Count occurrences
    entity_counts: Dict[str, int] = defaultdict(int)
    for entity in all_entities:
        key = entity.lower()
        entity_counts[key] += 1
    
    # Filter by query if provided
    if query:
        entity_counts = {
            k: v for k, v in entity_counts.items()
            if query.lower() in k
        }
    
    # Sort by frequency
    sorted_entities = sorted(
        entity_counts.items(),
        key=lambda x: x[1],
        reverse=True
    )[:limit]
    
    return [
        {"concept": concept, "mentions": count}
        for concept, count in sorted_entities
    ]
