"""Тесты KnowledgeStore (PLAN_SELF_LEARNING_AGENT этап 4)."""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from storage import KnowledgeStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "knowledge.db")


def _vec(n: int = 16) -> list[float]:
    """Простой test-vector (фактический dim=1536 в проде, но 16 ок для тестов)."""
    return [float(i) / n for i in range(n)]


def test_create_and_list_document(store):
    doc_id = store.create_document(
        account_id="acc1", filename="test.pdf",
        content_type="application/pdf", size_bytes=12345,
    )
    assert doc_id
    docs = store.list_documents("acc1")
    assert len(docs) == 1
    assert docs[0]["filename"] == "test.pdf"
    assert docs[0]["chunks_count"] == 0


def test_account_isolation(store):
    a = store.create_document(account_id="acc_a", filename="a.txt",
                              content_type="text/plain", size_bytes=100)
    b = store.create_document(account_id="acc_b", filename="b.txt",
                              content_type="text/plain", size_bytes=100)
    assert len(store.list_documents("acc_a")) == 1
    assert len(store.list_documents("acc_b")) == 1
    assert store.list_documents("acc_a")[0]["filename"] == "a.txt"


def test_insert_chunks_and_query(store):
    doc_id = store.create_document(
        account_id="acc1", filename="x.txt",
        content_type="text/plain", size_bytes=100,
    )
    chunks = [
        ("Привычки миллионеров — платить себе первым", _vec(), 10),
        ("Бюджет на 50/30/20 — старая школа", _vec(), 8),
        ("Ранний выход на пенсию через FIRE-стратегию", _vec(), 9),
    ]
    saved = store.insert_chunks(
        doc_id=doc_id, account_id="acc1", chunks=chunks, embedding_dim=16,
    )
    assert saved == 3
    docs = store.list_documents("acc1")
    assert docs[0]["chunks_count"] == 3

    # Query тестовый vector — должен вернуть top-K
    out = store.query(account_id="acc1", query_vec=_vec(), top_k=2)
    assert len(out) == 2
    assert out[0]["filename"] == "x.txt"
    assert "score" in out[0]
    assert out[0]["score"] >= out[1]["score"]


def test_query_isolated_per_account(store):
    da = store.create_document(account_id="acc_a", filename="a", content_type="text/plain", size_bytes=10)
    db = store.create_document(account_id="acc_b", filename="b", content_type="text/plain", size_bytes=10)
    store.insert_chunks(doc_id=da, account_id="acc_a",
                        chunks=[("text A", _vec(), 5)], embedding_dim=16)
    store.insert_chunks(doc_id=db, account_id="acc_b",
                        chunks=[("text B", _vec(), 5)], embedding_dim=16)
    out_a = store.query(account_id="acc_a", query_vec=_vec(), top_k=10)
    out_b = store.query(account_id="acc_b", query_vec=_vec(), top_k=10)
    assert [c["text"] for c in out_a] == ["text A"]
    assert [c["text"] for c in out_b] == ["text B"]


def test_delete_cascades_chunks(store):
    doc = store.create_document(account_id="acc", filename="d", content_type="text/plain", size_bytes=10)
    store.insert_chunks(doc_id=doc, account_id="acc",
                        chunks=[("t", _vec(), 5)], embedding_dim=16)
    assert store.stats("acc")["chunks"] == 1
    ok = store.delete_document(doc, "acc")
    assert ok is True
    assert store.list_documents("acc") == []
    assert store.stats("acc")["chunks"] == 0


def test_delete_wrong_account_returns_false(store):
    doc = store.create_document(account_id="acc_a", filename="d", content_type="text/plain", size_bytes=10)
    ok = store.delete_document(doc, "acc_b")
    assert ok is False
    assert len(store.list_documents("acc_a")) == 1


def test_query_empty_account_returns_empty(store):
    out = store.query(account_id="nobody", query_vec=_vec(), top_k=5)
    assert out == []


def test_stats(store):
    a = store.create_document(account_id="acc", filename="a", content_type="text/plain", size_bytes=10)
    b = store.create_document(account_id="acc", filename="b", content_type="text/plain", size_bytes=10)
    store.insert_chunks(doc_id=a, account_id="acc",
                        chunks=[("t1", _vec(), 5), ("t2", _vec(), 5)], embedding_dim=16)
    store.insert_chunks(doc_id=b, account_id="acc",
                        chunks=[("t3", _vec(), 5)], embedding_dim=16)
    s = store.stats("acc")
    assert s == {"documents": 2, "chunks": 3}
