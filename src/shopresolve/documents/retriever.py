"""Document-granularity retrieval shared by production snapshots and public IR evaluation."""

import re

import numpy as np
from rank_bm25 import BM25Okapi


def tokens(text):
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())


class DocumentRetriever:
    def __init__(self, identifiers, texts):
        self.identifiers = list(identifiers)
        self.lexical = BM25Okapi([tokens(text) or ["_"] for text in texts])

    def rank(self, query, limit=1000, *, require_match=False):
        terms = tokens(query)
        scores = self.lexical.get_scores(terms)
        # Stable ties follow corpus input order, never gold relevance labels.
        order = np.argsort(-scores, kind="stable")
        if require_match:
            order = [i for i in order if any(t in self.lexical.doc_freqs[int(i)] for t in terms)]
        order = order[:limit]
        return {self.identifiers[int(i)]: float(scores[i]) for i in order}


class SemanticDocumentRetriever(DocumentRetriever):
    """The same candidate/ranking implementation is used by tools and evaluations."""

    def __init__(self, identifiers, texts, encoder, *, vectors=None, reranker=None):
        self.texts = list(texts)
        super().__init__(identifiers, self.texts)
        self.encoder, self.reranker = encoder, reranker
        vectors = encoder.documents(self.texts) if vectors is None else vectors
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim != 2 or len(vectors) != len(self.identifiers):
            raise ValueError("Embeddings must align with the complete ordered corpus")
        if not np.isfinite(vectors).all():
            raise ValueError("Non-finite embeddings")
        self.vectors = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        self.positions = {key: i for i, key in enumerate(self.identifiers)}

    def rankings(self, query, limit=1000, *, query_vector=None):
        lexical = super().rank(query, limit)
        vector = np.asarray(
            self.encoder.query(query) if query_vector is None else query_vector, dtype=np.float32
        )
        if vector.shape != (self.vectors.shape[1],) or not np.isfinite(vector).all():
            raise ValueError("Invalid query embedding")
        vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
        scores = self.vectors @ vector
        order = np.argsort(-scores, kind="stable")[:limit]
        dense = {self.identifiers[int(i)]: float(scores[i]) for i in order}
        fusion = {}
        for ranking in (lexical, dense):
            for rank, key in enumerate(ranking, 1):
                fusion[key] = fusion.get(key, 0) + 1 / (60 + rank)
        fused = sorted(fusion, key=lambda k: (-fusion[k], self.positions[k]))[:limit]
        return {"bm25": lexical, "dense": dense, "hybrid": {k: fusion[k] for k in fused}}

    def rerank(self, query, ranking, *, candidate_k=20):
        if self.reranker is None:
            raise ValueError("A real cross-encoder is required; no silent rerank fallback")
        keys = list(ranking)
        candidates = keys[:candidate_k]
        scores = self.reranker.scores(query, [self.texts[self.positions[k]] for k in candidates])
        if len(scores) != len(candidates) or not np.isfinite(scores).all():
            raise ValueError("Invalid cross-encoder scores")
        rescored = sorted(zip(candidates, scores, strict=True), key=lambda p: -p[1])
        keys = [key for key, _ in rescored] + keys[candidate_k:]
        # A monotone rank score keeps the untouched tail below all reranked candidates.
        return {key: float(-rank) for rank, key in enumerate(keys, 1)}

    def rank(self, query, limit=1000, *, mode="hybrid"):
        if mode not in {"bm25", "dense", "hybrid", "hybrid_rerank"}:
            raise ValueError("Unknown document retrieval mode")
        if mode == "bm25":
            return super().rank(query, limit)
        ranks = self.rankings(query, max(limit, 20) if mode == "hybrid_rerank" else limit)
        if mode == "hybrid_rerank":
            return dict(list(self.rerank(query, ranks["hybrid"]).items())[:limit])
        return ranks[mode]
