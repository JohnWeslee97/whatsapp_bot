import re
import logging
from typing import List
from rank_bm25 import BM25Plus
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, BaseNode, QueryBundle

logger = logging.getLogger(__name__)


def tokenize_text(text: str) -> List[str]:
    """
    Tokenizes text into alphanumeric words and hyphenated codes (e.g. 'GRV-2026-X').
    """
    if not text:
        return []
    return re.findall(r"[\w\-]+", text.lower())


class PureBM25Retriever(BaseRetriever):
    """
    Pure-Python BM25 Keyword Retriever using BM25Plus.
    Ensures robust keyword search across document nodes without external C++ binary dependencies.
    """
    def __init__(self, nodes: List[BaseNode], similarity_top_k: int = 10):
        super().__init__()
        self.nodes = nodes
        self.similarity_top_k = similarity_top_k
        self.corpus = [tokenize_text(node.get_content()) for node in nodes]
        self.bm25 = BM25Plus(self.corpus) if self.corpus else None

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        if not self.bm25 or not self.nodes:
            return []

        query_tokens = tokenize_text(query_bundle.query_str)
        if not query_tokens:
            return []

        scores = self.bm25.get_scores(query_tokens)
        scored_nodes = [
            NodeWithScore(node=node, score=float(score))
            for node, score in zip(self.nodes, scores)
            if score > 0
        ]
        scored_nodes.sort(key=lambda x: x.score, reverse=True)
        return scored_nodes[:self.similarity_top_k]
