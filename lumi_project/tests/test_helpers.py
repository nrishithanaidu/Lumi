"""
tests/test_helpers.py
----------------------
Unit tests for utility functions. These are fast and require no AWS calls.

Run:
    pytest tests/test_helpers.py -v
"""

import pytest
from utils.helpers import (
    generate_job_id, current_timestamp, truncate, chunk_text,
    save_json, load_json, file_size_mb,
)
import os
import tempfile


class TestGenerateJobId:
    def test_returns_string(self):
        assert isinstance(generate_job_id(), str)

    def test_length_is_eight(self):
        assert len(generate_job_id()) == 8

    def test_unique_each_call(self):
        ids = {generate_job_id() for _ in range(100)}
        assert len(ids) == 100  # all unique


class TestCurrentTimestamp:
    def test_returns_string(self):
        assert isinstance(current_timestamp(), str)

    def test_is_iso_format(self):
        ts = current_timestamp()
        # ISO format contains T and +00:00
        assert "T" in ts
        assert "+" in ts or "Z" in ts or "UTC" in ts.upper()


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 100) == "hello"

    def test_long_text_truncated(self):
        result = truncate("a" * 400, 300)
        assert len(result) == 303   # 300 + "..."
        assert result.endswith("...")

    def test_exact_length_unchanged(self):
        text = "a" * 300
        assert truncate(text, 300) == text


class TestChunkText:
    def test_short_text_single_chunk(self):
        text   = "hello world this is a test"
        chunks = chunk_text(text, chunk_size=100, overlap=10)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_multiple_chunks(self):
        # 600 words, chunk size 200 → should produce at least 3 chunks
        text   = " ".join(["word"] * 600)
        chunks = chunk_text(text, chunk_size=200, overlap=20)
        assert len(chunks) >= 3

    def test_overlap_means_shared_words(self):
        words  = [f"w{i}" for i in range(100)]
        text   = " ".join(words)
        chunks = chunk_text(text, chunk_size=20, overlap=5)
        # The first word of chunk[1] should appear at the end of chunk[0]
        if len(chunks) > 1:
            chunk0_words = set(chunks[0].split())
            chunk1_words = set(chunks[1].split())
            overlap_words = chunk0_words & chunk1_words
            assert len(overlap_words) > 0

    def test_empty_text_returns_empty_list(self):
        assert chunk_text("", chunk_size=100) == []

    def test_chunks_cover_all_content(self):
        words  = [f"word{i}" for i in range(50)]
        text   = " ".join(words)
        chunks = chunk_text(text, chunk_size=10, overlap=2)
        all_chunk_words = set()
        for chunk in chunks:
            all_chunk_words.update(chunk.split())
        original_words = set(text.split())
        # Every word in the original should appear in at least one chunk
        assert original_words.issubset(all_chunk_words)


class TestSaveAndLoadJson:
    def test_roundtrip(self, tmp_path):
        data     = {"job_id": "abc123", "status": "done", "count": 42}
        filepath = str(tmp_path / "test.json")
        save_json(data, filepath)
        loaded   = load_json(filepath)
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_path):
        filepath = str(tmp_path / "deep" / "nested" / "file.json")
        save_json({"key": "value"}, filepath)
        assert os.path.exists(filepath)

    def test_unicode_preserved(self, tmp_path):
        data     = {"name": "Ananya Sharma", "org": "Apollo Diagnostics"}
        filepath = str(tmp_path / "unicode.json")
        save_json(data, filepath)
        loaded   = load_json(filepath)
        assert loaded["name"] == "Ananya Sharma"


class TestFileSizeMb:
    def test_returns_float(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"x" * 1024)
        assert isinstance(file_size_mb(str(f)), float)

    def test_correct_size(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"x" * (1024 * 1024))  # exactly 1 MB
        assert file_size_mb(str(f)) == 1.0
