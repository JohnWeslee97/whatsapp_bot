from django.core.management.base import BaseCommand
from chatbot.rag.ingest import build_index
import chromadb
from pathlib import Path
from django.conf import settings


class Command(BaseCommand):
    help = "Ingests company documents from knowledge_base/ into ChromaDB vector store."

    def handle(self, *args, **options):
        self.stdout.write("Indexing documents...")
        try:
            index = build_index()

            base_dir = getattr(settings, 'BASE_DIR', Path(__file__).resolve().parent.parent.parent.parent)
            vector_store_dir = Path(base_dir) / "vector_store"
            
            chroma_client = chromadb.PersistentClient(path=str(vector_store_dir))
            collection = chroma_client.get_or_create_collection(name="company_knowledge")
            chunks_count = collection.count()

            self.stdout.write(
                self.style.SUCCESS(f"Done. Indexed {chunks_count} chunks from knowledge_base/")
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error during ingestion: {e}"))
            raise e
