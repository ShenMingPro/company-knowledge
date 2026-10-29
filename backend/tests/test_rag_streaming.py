from types import SimpleNamespace
from threading import Event, Lock

import pytest
from langchain_core.documents import Document

from backend.app.adapters.rag import LegacyRagAdapter, RagUnavailableError
from rag.rag_service import RagSummarizeService


class StreamingChain:
    def __init__(self) -> None:
        self.stream_calls = []

    def invoke(self, _payload):
        raise AssertionError("流式问答不应调用 invoke")

    def stream(self, payload):
        self.stream_calls.append(payload)
        yield "第一段"
        yield "第二段"


def test_rag_summarize_uses_native_model_stream_and_returns_complete_result():
    service = RagSummarizeService.__new__(RagSummarizeService)
    service.use_query_rewrite = False
    service.query_rewriter = None
    service.use_hybrid = False
    service.hybrid_retriever = None
    service.use_reranker = False
    service.reranker = None
    service.model = SimpleNamespace(model_name="stream-test-model")
    service.chain = StreamingChain()

    document = Document(
        page_content="测试知识",
        metadata={"source_name": "manual.txt", "document_version_id": 1, "chunk_index": 0},
    )
    service.retriever_docs_with_scores = lambda _query: ([document], [0.1])
    service.validate_documents = lambda docs, scores: (docs, scores, None)
    service.build_context_with_citations = lambda _docs: "带引用的上下文"
    service.format_references = lambda _docs, _scores, _extra: [{"source_name": "manual.txt"}]

    chunks = []
    result = service.rag_summarize("测试问题", on_token=chunks.append)

    assert chunks == ["第一段", "第二段"]
    assert result["answer"] == "第一段第二段"
    assert result["has_answer"] is True
    assert result["references"] == [{"source_name": "manual.txt"}]
    assert service.chain.stream_calls == [{"input": "测试问题", "context": "带引用的上下文"}]


def test_adapter_turns_generation_error_after_partial_delta_into_stream_error():
    class FailingRag:
        def rag_summarize(self, _query, *, history, on_token):
            assert history is None
            on_token("半截")
            return {
                "answer": RagSummarizeService.REFUSAL_MESSAGE,
                "has_answer": False,
                "references": [],
                "refusal_reason": "generation_error",
            }

    adapter = LegacyRagAdapter()
    adapter._instances["employee"] = FailingRag()
    adapter._role_locks["employee"] = Lock()
    events = adapter.answer_stream(query="测试", role="employee")

    assert next(events) == {"type": "delta", "delta": "半截"}
    with pytest.raises(RagUnavailableError, match="生成失败"):
        next(events)


def test_adapter_preserves_delta_order_and_emits_done_last():
    result = {
        "answer": "第一段第二段",
        "has_answer": True,
        "references": [{"source_name": "manual.txt"}],
        "refusal_reason": None,
    }

    class SuccessfulRag:
        def rag_summarize(self, _query, *, history, on_token):
            assert history == [{"role": "user", "content": "上一问"}]
            on_token("第一段")
            on_token("第二段")
            return result

    adapter = LegacyRagAdapter()
    adapter._instances["employee"] = SuccessfulRag()
    adapter._role_locks["employee"] = Lock()

    assert list(
        adapter.answer_stream(
            query="测试",
            role="employee",
            history=[{"role": "user", "content": "上一问"}],
        )
    ) == [
        {"type": "delta", "delta": "第一段"},
        {"type": "delta", "delta": "第二段"},
        {"type": "done", "result": result},
    ]


def test_adapter_cancels_producer_and_releases_role_lock_when_consumer_closes():
    producer_stopped = Event()

    class LongRunningRag:
        def rag_summarize(self, _query, *, history, on_token):
            assert history is None
            try:
                for _ in range(10_000):
                    on_token("片段")
            finally:
                producer_stopped.set()
            return {"answer": "不会完成", "refusal_reason": None}

    adapter = LegacyRagAdapter()
    adapter._instances["employee"] = LongRunningRag()
    adapter._role_locks["employee"] = Lock()
    events = adapter.answer_stream(query="测试", role="employee")

    assert next(events) == {"type": "delta", "delta": "片段"}
    events.close()

    assert producer_stopped.wait(timeout=1)
    assert adapter._role_locks["employee"].acquire(timeout=1)
    adapter._role_locks["employee"].release()
