# Enterprise WhatsApp AI Chatbot with Hybrid RAG & Guardrails

An enterprise-ready AI Chatbot for WhatsApp powered by **Django**, **Meta WhatsApp Cloud API**, **Google Gemini AI**, **ChromaDB**, and **BM25 Search**.

---

## 📖 Quick Links
- 📘 **[Project Flow & Architecture Guide (PROJECT_DOCUMENTATION.md)](file:///d:/github/whats%20app%20chat-bot/PROJECT_DOCUMENTATION.md)**
- 🚀 **[How to Run & Test the Project (HOW_TO_RUN.md)](file:///d:/github/whats%20app%20chat-bot/HOW_TO_RUN.md)**
- 🧪 **[Automated Test Suite (test_pipeline.py)](file:///d:/github/whats%20app%20chat-bot/whatsapp_bot/test_pipeline.py)**
- 📄 **[Knowledge Base Policy Document (company_policy.md)](file:///d:/github/whats%20app%20chat-bot/whatsapp_bot/knowledge_base/company_policy.md)**

---

## 🚀 Key Features

1. **Meta WhatsApp Cloud API Integration**: Direct webhook integration with HMAC-SHA256 signature verification, read receipts, and real-time typing indicators.
2. **Hybrid RAG Pipeline**:
   - **Dense Vector Search (ChromaDB + Gemini Embeddings)** for conceptual / semantic similarity.
   - **Sparse BM25 Keyword Search (PureBM25)** for exact keyword matching (tracking codes, emails, policy numbers).
   - **Reciprocal Rank Fusion (RRF)** for optimal candidate scoring.
   - **LLM Reranking (Gemini Reranker)** to distill the top 3 highest-quality chunks.
3. **Multi-Turn Conversation Memory**: SQLite-backed conversation sessions tracking multi-turn dialog context.
4. **Safety & Guardrails**:
   - **Prompt Injection Guard**: Detects and blocks jailbreaks and adversarial prompts.
   - **Rate Limiter**: 10 requests per 60s per phone number sliding window.
   - **Faithfulness Validator**: Assesses answer grounding against context, triggering automatic re-generation if hallucination is detected.

---

## ⚡ Quick Start

### 1. Activate Environment & Run Pipeline Test
```bash
cd "d:\github\whats app chat-bot"
.venv\Scripts\activate
cd whatsapp_bot
python test_pipeline.py
```

### 2. Ingest Knowledge Base
```bash
python manage.py ingest_documents
```

### 3. Start Server & ngrok for Live WhatsApp
```bash
python manage.py runserver 8000
```
In another terminal:
```bash
ngrok http 8000
```
Set Webhook in Meta Developer Portal to `https://<ngrok-id>.ngrok-free.app/webhook/`.
