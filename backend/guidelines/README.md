# Place clinical guideline PDFs here for ingestion.

Supported sources (auto-detected from filename):
- NICE NG195 → region UK
- AAP 2023 → region USA  
- WHO Newborn Sepsis → region GLOBAL
- EOSCAL papers (Kuzniewicz 2024, Puopolo 2011/2019, Qatar validation) → GLOBAL

Run ingestion:
```
cd backend
python -m rag.ingest --dir ./guidelines
```

Or via API:
```
POST /api/v1/admin/guidelines/ingest
```
