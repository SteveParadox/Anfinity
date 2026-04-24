from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.search_highlights import SearchHighlightExtractor  # noqa: E402


def test_note_chunking_preserves_heading_and_offsets():
    extractor = SearchHighlightExtractor(target_chunk_chars=180, min_chunk_chars=20, max_chunk_chars=260)
    content = (
        "# Planning\n"
        "General workspace housekeeping belongs here.\n\n"
        "# Dyslexia Support\n"
        "The plan recommends guided reading, assistive technology, and structured literacy routines."
    )

    chunks = extractor.chunk_note(
        note_id="note-1",
        title="Education plan",
        content=content,
        tags=["education"],
    )

    assert len(chunks) >= 2
    target = next(chunk for chunk in chunks if chunk.heading == "Dyslexia Support")
    assert target.start_offset == content.index("# Dyslexia Support")
    assert target.end_offset <= len(content)
    assert content[target.start_offset : target.end_offset] == target.text


def test_highlight_selects_relevant_sentence_not_first_preview():
    extractor = SearchHighlightExtractor(target_chunk_chars=500, min_chunk_chars=20, max_chunk_chars=800)
    content = (
        "This opening sentence is generic and should not explain the match. "
        "The intervention plan uses structured literacy and guided reading for dyslexia support. "
        "The final sentence discusses meeting logistics."
    )
    chunk = extractor.chunk_note(note_id="note-2", title="Support plan", content=content)[0]

    highlights = extractor.extract_highlights(
        query="dyslexia reading intervention",
        chunk=chunk,
        source_type="note",
        max_highlights=1,
    )

    assert len(highlights) == 1
    assert "structured literacy" in highlights[0].text
    assert "opening sentence" not in highlights[0].text
    assert highlights[0].start_offset == content.index("The intervention plan")
    assert highlights[0].end_offset <= len(content)


def test_chunk_scoring_filters_unrelated_domain_content():
    extractor = SearchHighlightExtractor(target_chunk_chars=500, min_chunk_chars=20, max_chunk_chars=800)
    content = (
        "Bitcoin wallet volatility and exchange pricing are the main topics. "
        "Trading strategy notes mention market momentum and token liquidity."
    )
    chunks = extractor.chunk_note(note_id="note-3", title="Crypto notes", content=content, tags=["crypto"])

    matches = extractor.score_chunks(
        query="dyslexia reading intervention",
        chunks=chunks,
        note_semantic_score=0.35,
        note_text_score=0.0,
        source_type="note",
    )

    assert matches == []


def test_unrelated_highlight_returns_no_arbitrary_preview():
    extractor = SearchHighlightExtractor(target_chunk_chars=500, min_chunk_chars=20, max_chunk_chars=800)
    content = (
        "The opening paragraph talks about office snacks and calendar cleanup. "
        "The next sentence covers meeting rooms and printer access."
    )
    chunk = extractor.chunk_note(note_id="note-4", title="Office notes", content=content)[0]

    highlights = extractor.extract_highlights(
        query="oauth callback timeout",
        chunk=chunk,
        source_type="note",
        max_highlights=1,
    )

    assert highlights == []
