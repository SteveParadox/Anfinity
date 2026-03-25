"""Parsers for meeting transcripts from various sources."""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TranscriptEntry:
    """A single entry in a meeting transcript."""
    timestamp: str
    speaker: str
    text: str
    duration_ms: Optional[int] = None


class MeetingTranscriptParser:
    """Base parser for meeting transcripts."""
    
    @staticmethod
    def parse(file_path: str, file_content: str) -> Dict[str, Any]:
        """Parse meeting transcript.
        
        Auto-detects format based on content and extension.
        """
        if file_path.endswith('.vtt'):
            return MeetingTranscriptParser.parse_vtt(file_content)
        elif file_path.endswith('.json'):
            return MeetingTranscriptParser.parse_json(file_content)
        elif file_path.endswith('.srt'):
            return MeetingTranscriptParser.parse_srt(file_content)
        else:
            # Try to auto-detect
            return MeetingTranscriptParser.parse_auto(file_content)
    
    @staticmethod
    def parse_vtt(content: str) -> Dict[str, Any]:
        """Parse WebVTT transcript (Zoom format).
        
        Format: timestamp --> timestamp
                Speaker: Text
        """        
        entries = []
        lines = content.splitlines()
        for i in range(len(lines)):
            line = lines[i].strip()
            if '-->' in line:
                timestamps = line.split('-->')
                start_time = timestamps[0].strip()
                end_time = timestamps[1].strip()
                if i + 1 < len(lines):
                    speaker_line = lines[i + 1].strip()
                    if ':' in speaker_line:
                        speaker, text = speaker_line.split(':', 1)
                        entries.append(TranscriptEntry(
                            timestamp=start_time,
                            speaker=speaker.strip(),
                            text=text.strip(),
                            duration_ms=MeetingTranscriptParser.calculate_duration(start_time, end_time)
                        ))
        return {
            "format": "vtt",
            "entries": entries
        }
    
    @staticmethod
    def parse_json(content: str) -> Dict[str, Any]:
        """Parse JSON transcript (Otter.ai format).
        
        Format: [{"timestamp": "00:00:01", "speaker": "Alice", "text": "Hello"}]
        """
        import json
        data = json.loads(content)
        entries = []
        for item in data:
            entries.append(TranscriptEntry(
                timestamp=item.get("timestamp"),
                speaker=item.get("speaker"),
                text=item.get("text")
            ))
        return {
            "format": "json",
            "entries": entries
        }