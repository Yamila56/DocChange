"""Pinned, local-only ONNX models for optional document retrieval."""

import hashlib
import json
from pathlib import Path

import numpy as np

MODEL_LOCK = Path(__file__).resolve().parents[3] / "evals/document-retrieval-models.lock.json"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
TOKENIZER_FILES = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
]


def model_path(kind, cache_dir):
    from huggingface_hub import snapshot_download

    spec = json.loads(MODEL_LOCK.read_text(encoding="utf-8"))[kind]
    path = snapshot_download(
        spec["repository"],
        revision=spec["revision"],
        cache_dir=cache_dir,
        local_files_only=True,
        allow_patterns=[*TOKENIZER_FILES, spec["model_file"]],
    )
    return spec, Path(path)


class _Session:
    def __init__(self, kind, cache_dir, threads, provider):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        spec, path = model_path(kind, cache_dir)
        self.name = spec["model"]
        self.cache_signature = hashlib.sha256(
            (json.dumps(spec, sort_keys=True) + ort.__version__ + provider).encode()
        ).hexdigest()
        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(path / spec["model_file"]), sess_options=options, providers=[provider]
        )
        if self.session.get_providers()[0] != provider:
            raise RuntimeError("Requested ONNX provider unavailable; refusing silent fallback")
        self.tokenizer = Tokenizer.from_file(str(path / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.inputs = {item.name for item in self.session.get_inputs()}

    def run(self, texts):
        encoded = self.tokenizer.encode_batch(texts)
        fields = {
            "input_ids": "ids",
            "attention_mask": "attention_mask",
            "token_type_ids": "type_ids",
        }
        feed = {
            name: np.asarray([getattr(item, fields[name]) for item in encoded], dtype=np.int64)
            for name in self.inputs
        }
        return self.session.run(None, feed)[0]


class DocumentEncoder(_Session):
    def __init__(
        self, cache_dir=".cache/models", threads=4, provider="CPUExecutionProvider", batch_size=8
    ):
        super().__init__("encoder", cache_dir, threads, provider)
        self.document_batch_size = batch_size

    def documents(self, texts, batch_size=None):
        batch_size = batch_size or self.document_batch_size
        batches = []
        for start in range(0, len(texts), batch_size):
            # BGE uses the first (CLS) token, not mean pooling.
            vectors = self.run(texts[start : start + batch_size])[:, 0].astype(np.float32)
            vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
            batches.append(vectors)
        return np.vstack(batches)

    def queries(self, texts, batch_size=8):
        return self.documents([QUERY_PREFIX + text for text in texts], batch_size=batch_size)

    def query(self, text):
        return self.queries([text])[0]


class DocumentReranker(_Session):
    def __init__(self, cache_dir=".cache/models", threads=4, provider="CPUExecutionProvider"):
        super().__init__("reranker", cache_dir, threads, provider)

    def scores(self, query, documents):
        scores = []
        for start in range(0, len(documents), 8):
            scores.extend(self.run([(query, doc) for doc in documents[start : start + 8]])[:, 0])
        return [float(value) for value in scores]
