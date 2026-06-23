# NeoGuard AI Backend

FastAPI + LlamaIndex RAG pipeline (Section 7.3) + Gemini 2.0 Flash explanations.

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env
```

1. Create Supabase project and run [`../supabase/migrations/001_initial_schema.sql`](../supabase/migrations/001_initial_schema.sql)
2. Fill `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GEMINI_API_KEY` in `.env`
3. Place guideline PDFs in `guidelines/`
4. Ingest: `python -m rag.ingest --dir ./guidelines`
5. Run: `uvicorn main:app --reload --host 0.0.0.0 --port 8000`

## Ingest PDFs from external folder

```bash
python scripts/setup_guidelines.py --source "C:\path\to\pdfs"
```

## API Endpoints

- `GET /health`
- `POST /api/v1/evidence/search`
- `POST /api/v1/encounters/{id}/evidence`
- `POST /api/v1/encounters/{id}/explanation`
- `POST /api/v1/admin/guidelines` (PDF upload)
- `POST /api/v1/admin/guidelines/ingest`
- `GET /api/v1/admin/rag/health`

## Tests

```bash
pytest tests/ -v
```
