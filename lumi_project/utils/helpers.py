"""
utils/helpers.py
-----------------
Shared utility functions used across all phases.
"""

import os
import uuid
import json
from datetime import datetime, timezone


def generate_job_id() -> str:
    """Generate a short unique job ID (8 characters)."""
    return str(uuid.uuid4())[:8]


def current_timestamp() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def save_json(data: dict, filepath: str):
    """Save a dictionary as a formatted JSON file, creating dirs if needed."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_json(filepath: str) -> dict:
    """Load a JSON file and return it as a dictionary."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def file_size_mb(filepath: str) -> float:
    """Return the file size in MB, rounded to 2 decimal places."""
    return round(os.path.getsize(filepath) / (1024 * 1024), 2)


def truncate(text: str, max_chars: int = 300) -> str:
    """Truncate long text for display or DynamoDB storage."""
    return text[:max_chars] + "..." if len(text) > max_chars else text


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    Split text into overlapping chunks for RAG embedding (Phase 4).

    Args:
        text       : Full document text.
        chunk_size : Approximate words per chunk.
        overlap    : Words to repeat between consecutive chunks.

    Returns:
        List of text chunk strings.
    """
    words  = text.split()
    chunks = []
    start  = 0

    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        start += chunk_size - overlap

    return chunks
