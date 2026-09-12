from django.contrib import admin
from django.utils import timezone
from .models import Customer, Conversation, Message, Document, RequestLog
from chatbot.rag.ingest import build_index


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'name', 'message_count', 'last_active', 'created_at')
    search_fields = ('phone_number', 'name')
    list_filter = ('last_active', 'created_at')


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('customer', 'last_updated', 'created_at')
    list_filter = ('last_updated', 'created_at')
    search_fields = ('customer__phone_number', 'customer__name')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'role', 'text_preview', 'latency_ms', 'guardrail_passed', 'created_at')
    list_filter = ('role', 'guardrail_passed', 'created_at')
    search_fields = ('text', 'whatsapp_id', 'conversation__customer__phone_number')

    def text_preview(self, obj):
        return obj.text[:50] + "..." if len(obj.text) > 50 else obj.text


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('name', 'uploaded_at', 'indexed', 'indexed_at')
    list_filter = ('indexed', 'uploaded_at')
    search_fields = ('name',)
    actions = ['index_selected_documents']

    @admin.action(description="Index selected documents")
    def index_selected_documents(self, request, queryset):
        """
        Custom admin action to trigger RAG document indexing and update status.
        """
        try:
            # 1. Trigger ChromaDB vector indexing from ingest.py
            build_index()

            # 2. Update indexed flags and timestamps for selected documents
            now = timezone.now()
            count = 0
            for doc in queryset:
                doc.indexed = True
                doc.indexed_at = now
                doc.save(update_fields=['indexed', 'indexed_at'])
                count += 1

            self.message_user(request, f"Successfully indexed {count} document(s)")
        except Exception as e:
            self.message_user(request, f"Error during document indexing: {e}", level='ERROR')


@admin.register(RequestLog)
class RequestLogAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'short_message', 'status', 'latency_ms', 'timestamp')
    list_filter = ('status', 'timestamp')
    search_fields = ('phone_number', 'message_text')
    readonly_fields = ('phone_number', 'message_text', 'response_text', 'status', 'latency_ms', 'timestamp')
    ordering = ('-timestamp',)

    def short_message(self, obj):
        return obj.message_text[:50] + "..." if len(obj.message_text) > 50 else obj.message_text

    short_message.short_description = "Message"


