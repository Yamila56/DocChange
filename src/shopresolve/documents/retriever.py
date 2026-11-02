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

    def rank(self, query, limit=1000):
        scores = self.lexical.get_scores(tokens(query))
        # Stable ties follow corpus input order, never gold relevance labels.
        order = np.argsort(-scores, kind="stable")[:limit]
        return {self.identifiers[int(i)]: float(scores[i]) for i in order}
