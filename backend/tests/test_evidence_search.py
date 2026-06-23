"""API integration tests."""

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_evidence_search():
    r = client.post(
        "/api/v1/evidence/search",
        json={
            "query": "neonatal sepsis antibiotics benzylpenicillin",
            "active_guideline": "NICE",
            "limit": 5,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "chunk_id" in data[0]
    assert "similarity_score" in data[0]


def test_rag_health():
    r = client.get("/api/v1/admin/rag/health")
    assert r.status_code == 200
    body = r.json()
    assert "embedding_model" in body
    assert "total_chunks" in body
