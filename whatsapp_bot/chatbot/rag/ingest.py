import os
import logging
from pathlib import Path
from django.conf import settings

import chromadb
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex, StorageContext, Settings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.gemini import GeminiEmbedding

logger = logging.getLogger(__name__)


def build_index() -> VectorStoreIndex:
    """
    Builds and persists a vector index from documents in knowledge_base/
    
    1. Load all documents from knowledge_base/ folder using SimpleDirectoryReader
       (supports PDF, TXT, DOCX automatically)
    2. Split into chunks using SentenceSplitter:
       - chunk_size = 512
       - chunk_overlap = 50
    3. Create embeddings using GeminiEmbedding:
       - model = "models/embedding-001"
       - api_key from GEMINI_API_KEY environment variable
    4. Store in ChromaDB:
       - PersistentClient at path = "./vector_store"
       - collection_name = "company_knowledge"
    5. Return the VectorStoreIndex
    """
    base_dir = getattr(settings, 'BASE_DIR', Path(__file__).resolve().parent.parent.parent)
    knowledge_base_dir = Path(base_dir) / "knowledge_base"
    vector_store_dir = Path(base_dir) / "vector_store"

    knowledge_base_dir.mkdir(parents=True, exist_ok=True)
    vector_store_dir.mkdir(parents=True, exist_ok=True)

    api_key = getattr(settings, 'GEMINI_API_KEY', os.getenv('GEMINI_API_KEY', ''))
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is missing!")

    os.environ["GOOGLE_API_KEY"] = api_key

    # 1. Load documents from knowledge_base/
    reader = SimpleDirectoryReader(input_dir=str(knowledge_base_dir))
    documents = reader.load_data()

    # 2. Split into chunks using SentenceSplitter (chunk_size=512, chunk_overlap=50)
    text_splitter = SentenceSplitter(
        chunk_size=512,
        chunk_overlap=50
    )

    # 3. Create embeddings using GeminiEmbedding (model="models/gemini-embedding-001")
    embed_model = GeminiEmbedding(
        model_name="models/gemini-embedding-001",
        api_key=api_key
    )

    Settings.embed_model = embed_model
    Settings.text_splitter = text_splitter

    # 4. Store in ChromaDB (PersistentClient at "./vector_store", collection="company_knowledge")
    chroma_client = chromadb.PersistentClient(path=str(vector_store_dir))
    chroma_collection = chroma_client.get_or_create_collection(name="company_knowledge")

    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    # 5. Build and return VectorStoreIndex
    index = VectorStoreIndex.from_documents(
        documents=documents,
        storage_context=storage_context,
        transformations=[text_splitter],
        embed_model=embed_model,
        show_progress=True
    )

    return index
