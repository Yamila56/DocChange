import hashlib
import json
import re
from pathlib import Path
from threading import Lock

import numpy as np
from rank_bm25 import BM25Okapi

from shopresolve.service import digest


def tokenize(text):
    latin = re.findall(r"[a-z0-9_]+", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]", text)
    return latin + chinese + [a + b for a, b in zip(chinese, chinese[1:], strict=False)]


class NeuralEncoder:
    def __init__(self, name="BAAI/bge-small-zh-v1.5", cache_dir=".cache/models"):
        from fastembed import TextEmbedding

        self.name = name
        self.model = TextEmbedding(name, cache_dir=cache_dir, threads=2, local_files_only=True)

    def documents(self, texts):
        return np.asarray(list(self.model.passage_embed(texts)), dtype=np.float32)

    def query(self, text):
        return np.asarray(list(self.model.query_embed(text))[0], dtype=np.float32)


class NeuralReranker:
    def __init__(self, name="BAAI/bge-reranker-v2-m3-int8", cache_dir=".cache/models"):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.name = name
        self.model = TextCrossEncoder(name, cache_dir=cache_dir, threads=2, local_files_only=True)

    def scores(self, query, documents):
        return list(self.model.rerank(query, documents, batch_size=4))


class PolicyIndex:
    def __init__(self, path, *, encoder=None, reranker=None, mode="bm25", expand_parent=True):
        raw = Path(path).read_bytes()
        corpus = json.loads(raw)
        if mode not in {"bm25", "dense", "hybrid"}:
            raise ValueError("Unknown retrieval mode")
        if mode != "bm25" and encoder is None:
            raise ValueError(
                "Dense/hybrid requires a real encoder; no silent fake embedding fallback"
            )
        self.snapshot_id = hashlib.sha256(raw).hexdigest()
        self.policy_bundle_hash = digest(corpus["rule_config"])
        self.documents = corpus["documents"]
        self.encoder, self.reranker, self.mode = encoder, reranker, mode
        self.expand_parent = expand_parent
        self.lock = Lock()
        self.children = []
        for parent in self.documents:
            sentences = [s.strip() for s in re.split(r"(?<=[。！？])", parent["text"]) if s.strip()]
            for index, sentence in enumerate(sentences):
                self.children.append(
                    {
                        **parent,
                        "parent_id": parent["id"],
                        "id": f"{parent['id']}:{index}",
                        "child_text": sentence,
                    }
                )
        self.texts = [c["title"] + " " + c["child_text"] for c in self.children]
        self.bm25 = BM25Okapi([tokenize(text) for text in self.texts])
        self.vectors = None
        if encoder:
            vectors = encoder.documents(self.texts)
            self.vectors = vectors / np.maximum(
                np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12
            )

    def manifest(self):
        return {
            "snapshot_id": self.snapshot_id,
            "mode": self.mode,
            "embedding_model": self.encoder.name if self.encoder else None,
            "reranker_model": self.reranker.name if self.reranker else None,
            "expand_parent": self.expand_parent,
            "document_count": len(self.documents),
            "child_count": len(self.children),
            "synthetic": True,
        }

    def search(self, query, *, policy_version, owner_id=None, top_k=6, candidate_k=20):
        if not 1 <= top_k <= 20 or not 1 <= candidate_k <= 100:
            raise ValueError("Retrieval limit out of range")
        eligible = [
            i
            for i, doc in enumerate(self.children)
            if doc["version"] == policy_version
            and (not doc.get("owner_id") or doc["owner_id"] == owner_id)
        ]
        if not eligible:
            return []
        with self.lock:
            lexical = self.bm25.get_scores(tokenize(query))
            bm_rank = sorted(eligible, key=lambda i: (-float(lexical[i]), i))[:candidate_k]
            dense_rank = []
            if self.mode != "bm25":
                vector = self.encoder.query(query)
                vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
                scores = self.vectors @ vector
                dense_rank = sorted(eligible, key=lambda i: (-float(scores[i]), i))[:candidate_k]
            if self.mode == "bm25":
                ranked = bm_rank
            elif self.mode == "dense":
                ranked = dense_rank
            else:
                fusion = {}
                for ranking in (bm_rank, dense_rank):
                    for rank, idx in enumerate(ranking, 1):
                        fusion[idx] = fusion.get(idx, 0) + 1 / (60 + rank)
                ranked = sorted(fusion, key=lambda i: (-fusion[i], i))[:30]
            if self.reranker and ranked:
                scores = self.reranker.scores(query, [self.texts[i] for i in ranked])
                ranked = [
                    i
                    for _, i in sorted(
                        zip(scores, ranked, strict=True), key=lambda pair: (-pair[0], pair[1])
                    )
                ]
        results, parents = [], set()
        for idx in ranked:
            doc = self.children[idx]
            if doc["parent_id"] in parents:
                continue
            parents.add(doc["parent_id"])
            quote = doc["text"] if self.expand_parent else doc["child_text"]
            results.append(
                {
                    "id": f"{doc['version']}:{doc['id']}",
                    "version": doc["version"],
                    "parent_id": doc["parent_id"],
                    "locator": doc["id"],
                    "quote": quote,
                    "title": doc["title"],
                    "content_hash": digest(quote),
                    "snapshot_id": self.snapshot_id,
                }
            )
            if len(results) >= top_k:
                break
        return results


def build_index(settings):
    encoder = (
        NeuralEncoder(settings.embedding_model, settings.model_cache_dir)
        if settings.retrieval_mode != "bm25"
        else None
    )
    reranker = (
        NeuralReranker(settings.reranker_model, settings.model_cache_dir)
        if settings.enable_reranker
        else None
    )
    return PolicyIndex(
        settings.policy_corpus, encoder=encoder, reranker=reranker, mode=settings.retrieval_mode
    )
