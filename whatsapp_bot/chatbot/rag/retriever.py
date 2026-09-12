import os
import logging
from pathlib import Path
from typing import List
from django.conf import settings

import chromadb
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.postprocessor import LLMRerank
from llama_index.core.schema import TextNode, NodeWithScore
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.gemini import GeminiEmbedding
from llama_index.llms.gemini import Gemini

from chatbot.rag.bm25_retriever import PureBM25Retriever

logger = logging.getLogger(__name__)


def retrieve_context(query_text: str) -> str:
    """
    Hybrid Context Retriever (Dense Vector Search + BM25 Keyword Search + LLM Reranking).

    Workflow:
    1. Connects to ChromaDB vector store ('company_knowledge').
    2. Builds Vector Retriever (Dense semantic similarity, top_k=10).
    3. Builds BM25 Keyword Retriever (Exact keyword matching, top_k=10).
    4. Fuses Vector + BM25 results using Reciprocal Rank Fusion (RRF).
    5. Reranks candidate nodes with Gemini LLMRerank to select Top-3 highest quality chunks.
    6. Returns combined context string joined with "\\n\\n---\\n\\n".
    """
    if not query_text or not query_text.strip():
        return ""

    base_dir = getattr(settings, 'BASE_DIR', Path(__file__).resolve().parent.parent.parent)
    vector_store_dir = Path(base_dir) / "vector_store"

    api_key = getattr(settings, 'GEMINI_API_KEY', os.getenv('GEMINI_API_KEY', ''))
    if not api_key:
        logger.error("[RAG] GEMINI_API_KEY is not configured!")
        return ""

    os.environ["GOOGLE_API_KEY"] = api_key

    try:
        # 1. Connect to ChromaDB Vector Store
        if not vector_store_dir.exists():
            return ""

        chroma_client = chromadb.PersistentClient(path=str(vector_store_dir))
        chroma_collection = chroma_client.get_or_create_collection(name="company_knowledge")

        if chroma_collection.count() == 0:
            return ""

        embed_model = GeminiEmbedding(
            model_name="models/gemini-embedding-001",
            api_key=api_key
        )
        gemini_model_name = getattr(settings, 'GEMINI_MODEL', os.getenv('GEMINI_MODEL', 'gemini-2.0-flash'))
        gemini_llm = Gemini(
            model_name=f"models/{gemini_model_name}" if not gemini_model_name.startswith("models/") else gemini_model_name,
            api_key=api_key,
            temperature=0.0
        )

        Settings.embed_model = embed_model
        Settings.llm = gemini_llm

        # Load Chroma Vector Store Index
        vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

        index = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            storage_context=storage_context,
            embed_model=embed_model
        )

        # 2. Dense Vector Retriever (Semantic search, top_k=10)
        vector_retriever = index.as_retriever(similarity_top_k=10)
        try:
            vec_nodes = vector_retriever.retrieve(query_text)
            print(f"\n[RAG - VECTOR SEARCH] Query: '{query_text}' | Found {len(vec_nodes)} candidate nodes", flush=True)
            for idx, vn in enumerate(vec_nodes[:3], 1):
                snippet = vn.node.get_content().strip().replace('\n', ' ')[:150]
                score_str = f" (score: {vn.score:.4f})" if vn.score is not None else ""
                print(f"   [Vector #{idx}{score_str}] \"{snippet}...\"", flush=True)
        except Exception as v_err:
            print(f"   [Vector Search Error] {v_err}", flush=True)

        # 3. Sparse BM25 Keyword Retriever (Exact token matching, top_k=10)
        # Fetch all document nodes from ChromaDB to populate BM25 corpus
        raw_docs = chroma_collection.get()
        doc_texts = raw_docs.get("documents", [])
        doc_metadatas = raw_docs.get("metadatas", [])
        doc_ids = raw_docs.get("ids", [])

        all_nodes = []
        for i, text in enumerate(doc_texts):
            meta = doc_metadatas[i] if i < len(doc_metadatas) else {}
            node_id = doc_ids[i] if i < len(doc_ids) else f"node_{i}"
            all_nodes.append(TextNode(text=text, id_=node_id, metadata=meta or {}))

        bm25_retriever = PureBM25Retriever(nodes=all_nodes, similarity_top_k=10)
        try:
            bm25_nodes = bm25_retriever.retrieve(query_text)
            print(f"\n[RAG - BM25 KEYWORD SEARCH] Query: '{query_text}' | Found {len(bm25_nodes)} candidate nodes", flush=True)
            for idx, bn in enumerate(bm25_nodes[:3], 1):
                snippet = bn.node.get_content().strip().replace('\n', ' ')[:150]
                score_str = f" (BM25 score: {bn.score:.4f})" if bn.score is not None else ""
                print(f"   [BM25 #{idx}{score_str}] \"{snippet}...\"", flush=True)
        except Exception as b_err:
            print(f"   [BM25 Search Error] {b_err}", flush=True)

        # 4. Hybrid Fusion Retriever (Vector + BM25 using Reciprocal Rank Fusion)
        hybrid_retriever = QueryFusionRetriever(
            retrievers=[vector_retriever, bm25_retriever],
            similarity_top_k=10,
            num_queries=1,
            mode="reciprocal_rerank",
            use_async=False,
        )

        retrieved_nodes = hybrid_retriever.retrieve(query_text)

        if not retrieved_nodes:
            print(f"[RAG - HYBRID FUSION] No matching candidate nodes found.", flush=True)
            return ""

        # 5. Rerank candidate nodes with Gemini LLMRerank (Top 3)
        try:
            reranker = LLMRerank(
                choice_batch_size=5,
                top_n=3,
                llm=gemini_llm
            )
            
            reranked_nodes = reranker.postprocess_nodes(
                nodes=retrieved_nodes,
                query_str=query_text
            )
        except Exception as rerank_err:
            logger.warning(f"[RAG WARNING] LLMRerank failed ({rerank_err}). Falling back to top hybrid chunks.")
            reranked_nodes = retrieved_nodes[:3]

        if not reranked_nodes:
            reranked_nodes = retrieved_nodes[:3]

        # 6. Combine top chunks
        chunks = [node.node.get_content().strip() for node in reranked_nodes if node.node.get_content()]
        combined_context = "\n\n---\n\n".join(chunks)

        print(f"\n[RAG - HYBRID RERANK FINAL] Selected top {len(chunks)} chunks for answer generation.\n", flush=True)
        logger.info(f"[HYBRID RAG] Retrieved {len(chunks)} hybrid chunks for query: '{query_text}'")
        return combined_context

    except Exception as e:
        logger.error(f"[RAG ERROR] Failed in hybrid retrieval: {e}", exc_info=True)
        return ""


def retrieve_vector_context(query_text: str, top_k: int = 3) -> str:
    """
    Dense Vector Similarity Search only.
    """
    if not query_text or not query_text.strip():
        return ""

    base_dir = getattr(settings, 'BASE_DIR', Path(__file__).resolve().parent.parent.parent)
    vector_store_dir = Path(base_dir) / "vector_store"
    api_key = getattr(settings, 'GEMINI_API_KEY', os.getenv('GEMINI_API_KEY', ''))
    if not api_key or not vector_store_dir.exists():
        return ""

    try:
        chroma_client = chromadb.PersistentClient(path=str(vector_store_dir))
        chroma_collection = chroma_client.get_or_create_collection(name="company_knowledge")
        if chroma_collection.count() == 0:
            return ""

        embed_model = GeminiEmbedding(model_name="models/gemini-embedding-001", api_key=api_key)
        vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            storage_context=storage_context,
            embed_model=embed_model
        )
        retriever = index.as_retriever(similarity_top_k=top_k)
        nodes = retriever.retrieve(query_text)
        chunks = [n.node.get_content().strip() for n in nodes if n.node.get_content()]
        return "\n\n---\n\n".join(chunks)
    except Exception as e:
        logger.error(f"[VECTOR RAG ERROR] {e}", exc_info=True)
        return ""


def retrieve_bm25_context(query_text: str, top_k: int = 3) -> str:
    """
    Sparse BM25 Keyword Search only.
    """
    if not query_text or not query_text.strip():
        return ""

    base_dir = getattr(settings, 'BASE_DIR', Path(__file__).resolve().parent.parent.parent)
    vector_store_dir = Path(base_dir) / "vector_store"
    if not vector_store_dir.exists():
        return ""

    try:
        chroma_client = chromadb.PersistentClient(path=str(vector_store_dir))
        chroma_collection = chroma_client.get_or_create_collection(name="company_knowledge")
        if chroma_collection.count() == 0:
            return ""

        raw_docs = chroma_collection.get()
        doc_texts = raw_docs.get("documents", [])
        doc_metadatas = raw_docs.get("metadatas", [])
        doc_ids = raw_docs.get("ids", [])

        all_nodes = []
        for i, text in enumerate(doc_texts):
            meta = doc_metadatas[i] if i < len(doc_metadatas) else {}
            node_id = doc_ids[i] if i < len(doc_ids) else f"node_{i}"
            all_nodes.append(TextNode(text=text, id_=node_id, metadata=meta or {}))

        bm25_retriever = PureBM25Retriever(nodes=all_nodes, similarity_top_k=top_k)
        nodes = bm25_retriever.retrieve(query_text)
        chunks = [n.node.get_content().strip() for n in nodes if n.node.get_content()]
        return "\n\n---\n\n".join(chunks)
    except Exception as e:
        logger.error(f"[BM25 RAG ERROR] {e}", exc_info=True)
        return ""

