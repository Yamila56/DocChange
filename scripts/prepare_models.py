"""Download public ONNX retrieval weights, never an LLM or paid endpoint."""

from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder


def main():
    TextEmbedding("BAAI/bge-small-zh-v1.5", cache_dir=".cache/models", threads=2)
    print("Embedding model ready: BAAI/bge-small-zh-v1.5")
    TextCrossEncoder("BAAI/bge-reranker-v2-m3-int8", cache_dir=".cache/models", threads=2)
    print("Reranker ready: BAAI/bge-reranker-v2-m3-int8")


if __name__ == "__main__":
    main()
