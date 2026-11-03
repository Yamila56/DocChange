"""Section retrieval over authorized raw text; no labels or generative summaries."""

import hashlib
import re
import sqlite3
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path

import numpy as np

from .neural import MODEL_LOCK, DocumentEncoder
from .retriever import DocumentRetriever, SemanticDocumentRetriever


def change_summary(diff):
    """Summarize syntax only; this never asserts that public behavior changed."""
    changed = re.findall(r"^diff --git a/(.*?) b/.*$", diff, re.M)
    changed_lines = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )[:4_000_000]
    terms = list(
        dict.fromkeys(
            re.findall(
                r"--[a-z][a-z0-9-]+|\b[A-Z][A-Z0-9_]{3,}\b|\b[a-z]+_[a-z0-9_]+\b", changed_lines
            )
        )
    )
    return {
        "changed_paths": changed[:200],
        "seed_terms": terms[:80],
        "diff_excerpt": diff[:12000],
        "excerpt_truncated": len(diff) > 12000,
        "scope": "Deterministic syntax summary; not a verified behavior/coverage judgment",
    }


def sections(path, text):
    """Keep headings as context and exact character offsets for every raw snippet."""
    lines = text.splitlines(keepends=True)
    offsets, current = [], 0
    for line in lines:
        offsets.append(current)
        current += len(line)
    headings, contexts, fence = [], [], None
    for i, line in enumerate(lines):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker[1][0]
            fence = None if fence == token else (token if fence is None else fence)
        if fence is None:
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.rstrip())
            if heading:
                level, title = len(heading[1]), heading[2]
                headings = [h for h in headings if h[0] < level] + [(level, title)]
            elif (
                i + 1 < len(lines)
                and line.strip()
                and re.fullmatch(r"[=\-~^]{3,}", lines[i + 1].strip())
            ):
                headings = [(1, line.strip())]
        contexts.append(" > ".join(title for _, title in headings))
    result, start = [], 0
    while start < len(lines):
        end = start + 1
        while end < len(lines) and end - start < 50:
            if contexts[end] != contexts[start] or offsets[end] - offsets[start] >= 1600:
                break
            end += 1
        first, last = offsets[start], offsets[end] if end < len(lines) else len(text)
        # Even a single long line is divided without losing its exact observed range.
        for begin in range(first, last, 1800):
            finish = min(begin + 1800, last)
            quote = text[begin:finish]
            if not quote.strip():
                continue
            result.append(
                {
                    "path": path,
                    "heading": contexts[start],
                    "start_line": bisect_right(offsets, begin),
                    "end_line": bisect_right(offsets, max(begin, finish - 1)),
                    "start_char": begin,
                    "end_char": finish,
                    "content": quote,
                }
            )
        start = end
    return result


@lru_cache(maxsize=2)
def encoder_for(cache_dir, provider, batch_size=8):
    return DocumentEncoder(cache_dir, provider=provider, batch_size=batch_size)


def cached_vectors(encoder, texts, cache_dir):
    signature = getattr(
        encoder, "cache_signature", hashlib.sha256(MODEL_LOCK.read_bytes()).hexdigest()
    )
    path = Path(cache_dir) / "repository-embeddings.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    result, missing = [None] * len(texts), []
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, value BLOB)")
        for i, text in enumerate(texts):
            key = hashlib.sha256((signature + text).encode()).hexdigest()
            row = db.execute("SELECT value FROM embeddings WHERE key=?", (key,)).fetchone()
            if row:
                value = np.frombuffer(row[0], dtype=np.float32)
                if value.shape != (768,) or not np.isfinite(value).all():
                    raise ValueError("Invalid cached repository embedding")
                result[i] = value
            else:
                missing.append((i, key))
        for start in range(0, len(missing), 128):
            batch = missing[start : start + 128]
            vectors = encoder.documents([texts[i] for i, _ in batch])
            for (i, key), vector in zip(batch, vectors, strict=True):
                result[i] = vector
                db.execute(
                    "INSERT OR REPLACE INTO embeddings VALUES (?,?)", (key, vector.tobytes())
                )
            db.commit()
        return np.asarray(result, dtype=np.float32)


class RepositorySearch:
    def __init__(self, documents, *, encoder=None, cache_dir=".cache/models"):
        self.documents = documents
        self.parts = [part for path, text in documents.items() for part in sections(path, text)]
        self.ids = [str(i) for i in range(len(self.parts))]
        self.texts = [p["path"] + "\n" + p["heading"] + "\n" + p["content"] for p in self.parts]
        self.lexical = DocumentRetriever(self.ids, self.texts) if self.parts else None
        self.encoder, self.cache_dir, self.semantic = encoder, cache_dir, None

    def rank(self, queries, mode="hybrid", limit=1000):
        if not self.parts:
            return {}
        if mode not in {"section_bm25", "dense", "hybrid"}:
            raise ValueError("Unknown repository retrieval mode")
        if mode != "section_bm25" and self.semantic is None:
            encoder = self.encoder or encoder_for(self.cache_dir, "CPUExecutionProvider")
            vectors = cached_vectors(encoder, self.texts, self.cache_dir)
            self.semantic = SemanticDocumentRetriever(
                self.ids, self.texts, encoder, vectors=vectors
            )
        fusion = {}
        for query in queries:
            ranking = (
                self.lexical.rank(query, limit, require_match=True)
                if mode == "section_bm25"
                else self.semantic.rank(query, limit, mode=mode)
            )
            for position, key in enumerate(ranking, 1):
                fusion[key] = fusion.get(key, 0) + 1 / (60 + position)
        return {
            key: fusion[key] for key in sorted(fusion, key=lambda k: (-fusion[k], int(k)))[:limit]
        }

    def search(self, queries, mode="hybrid", limit=12, offset=0):
        ranking = self.rank(queries, mode)
        # Put two sections per file first, while retaining the rest for pagination.
        counts, diverse, tail = {}, [], []
        for key in ranking:
            path = self.parts[int(key)]["path"]
            counts[path] = counts.get(path, 0) + 1
            (diverse if counts[path] <= 2 else tail).append(key)
        ordered = diverse + tail
        return [
            {**self.parts[int(key)], "score": ranking[key]}
            for key in ordered[offset : offset + limit]
        ]
