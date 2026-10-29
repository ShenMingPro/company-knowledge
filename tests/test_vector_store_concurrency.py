from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import rag.vector_store as vector_store_module
from langchain_core.documents import Document


@pytest.fixture(autouse=True)
def reset_shared_vector_store(monkeypatch):
    monkeypatch.setattr(vector_store_module, "_VECTOR_STORE", None)
    yield
    vector_store_module._VECTOR_STORE = None


def test_concurrent_services_share_one_chroma_client(monkeypatch):
    """Parallel upload tasks must not initialize Chroma concurrently."""

    state_lock = threading.Lock()
    active_initializers = 0
    init_calls = 0

    class RaceSensitiveChroma:
        def __init__(self, **_kwargs):
            nonlocal active_initializers, init_calls
            with state_lock:
                init_calls += 1
                active_initializers += 1
                overlapping = active_initializers > 1
            time.sleep(0.02)
            with state_lock:
                active_initializers -= 1
            if overlapping:
                raise ValueError("Could not connect to tenant default_tenant")

    monkeypatch.setattr(vector_store_module, "Chroma", RaceSensitiveChroma)

    errors = []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(vector_store_module.VectorStoreService) for _ in range(20)]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:  # pragma: no cover - assertion reports exact failures
                errors.append(exc)

    assert errors == []
    assert init_calls == 1


def test_concurrent_indexes_serialize_local_chroma_writes(monkeypatch):
    active_writes = 0
    max_active_writes = 0
    state_lock = threading.Lock()

    class WriteSensitiveStore:
        def add_documents(self, _documents):
            nonlocal active_writes, max_active_writes
            with state_lock:
                active_writes += 1
                max_active_writes = max(max_active_writes, active_writes)
            time.sleep(0.01)
            with state_lock:
                active_writes -= 1

    class PassthroughRouter:
        @staticmethod
        def split(documents, _source_type):
            return documents

    service = object.__new__(vector_store_module.VectorStoreService)
    service.vector_store = WriteSensitiveStore()
    service.chunk_router = PassthroughRouter()
    service._parse_document = lambda _path: [Document(page_content="content")]
    service._get_source_type = lambda _path: "txt"
    monkeypatch.setattr(
        vector_store_module.DocumentService,
        "update_version_status",
        lambda *_args, **_kwargs: True,
    )

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(
            lambda version_id: service.index_document(
                file_path=f"document-{version_id}.txt",
                document_id=version_id,
                version_id=version_id,
                space_id=1,
                document_name=f"document-{version_id}.txt",
            ),
            range(20),
        ))

    assert all(result["success"] for result in results)
    assert max_active_writes == 1
