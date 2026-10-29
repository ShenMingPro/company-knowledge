"""
Reranker 单元测试
测试 DashScope Reranker 的各种场景（使用 Mock，不产生真实 API 调用）
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from langchain_core.documents import Document
from rag.reranker import Reranker, create_reranker, RerankOutcome
from rag.hybrid_retriever import RetrievalCandidate


class TestRerankerConfig:
    """测试 Reranker 配置和初始化"""

    def test_valid_config(self):
        """测试：有效配置正常初始化"""
        config = {
            'enabled': True,
            'model': 'gte-rerank-v2',
            'candidate_k': 10,
            'output_top_k': 3,
            'max_query_chars': 500,
            'max_document_chars': 1200,
            'timeout_seconds': 8
        }

        reranker = Reranker(config)
        assert reranker.enabled is True
        assert reranker.model == 'gte-rerank-v2'
        assert reranker.candidate_k == 10
        assert reranker.output_top_k == 3

    def test_invalid_config_candidate_less_than_output(self):
        """测试：candidate_k < output_top_k 时配置无效"""
        config = {
            'enabled': True,
            'candidate_k': 2,
            'output_top_k': 5
        }

        reranker = Reranker(config)
        assert reranker.enabled is False

    def test_disabled_config(self):
        """测试：禁用配置"""
        config = {'enabled': False}
        reranker = create_reranker(config)
        assert reranker is None


class TestRerankerFallback:
    """测试 Reranker 降级场景"""

    def test_disabled_fallback(self):
        """测试：Reranker 禁用时降级"""
        config = {'enabled': False}
        reranker = Reranker(config)

        candidates = [self._create_candidate(i) for i in range(5)]
        outcome = reranker.rerank("测试问题", candidates)

        assert outcome.applied is False
        assert outcome.fallback_reason == "disabled"
        assert len(outcome.candidates) == 3  # 默认 output_top_k

    def test_no_api_key_fallback(self):
        """测试：缺少 API Key 时降级"""
        config = {'enabled': True}

        with patch.dict('os.environ', {}, clear=True):
            reranker = Reranker(config)
            candidates = [self._create_candidate(i) for i in range(5)]
            outcome = reranker.rerank("测试问题", candidates)

            assert outcome.applied is False
            assert outcome.fallback_reason == "missing_api_key"

    def test_insufficient_candidates_fallback(self):
        """测试：候选数不足时降级"""
        config = {'enabled': True, 'output_top_k': 3}

        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            reranker = Reranker(config)
            candidates = [self._create_candidate(i) for i in range(2)]
            outcome = reranker.rerank("测试问题", candidates)

            assert outcome.applied is False
            assert outcome.fallback_reason == "insufficient_candidates"

    def test_unverifiable_metadata_fallback(self):
        """测试：候选缺少元数据时拒绝发送"""
        config = {'enabled': True}

        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            reranker = Reranker(config)

            # 创建缺少元数据的候选
            doc = Document(page_content="测试内容", metadata={})
            cand = RetrievalCandidate(doc, "unknown_0")
            candidates = [cand] * 5

            outcome = reranker.rerank("测试问题", candidates)

            assert outcome.applied is False
            assert outcome.fallback_reason == "unverifiable_candidate_metadata"

    def test_api_error_fallback(self):
        """测试：API 调用失败时降级"""
        config = {'enabled': True}

        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            reranker = Reranker(config)
            candidates = [self._create_candidate(i) for i in range(5)]

            # Mock DashScope 返回错误
            with patch('dashscope.TextReRank.call') as mock_call:
                mock_response = Mock()
                mock_response.status_code = 500
                mock_response.message = "Internal Server Error"
                mock_call.return_value = mock_response

                outcome = reranker.rerank("测试问题", candidates)

                assert outcome.applied is False
                assert outcome.fallback_reason == "api_error"

    def test_empty_response_fallback(self):
        """测试：API 返回空结果时降级"""
        config = {'enabled': True}

        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            reranker = Reranker(config)
            candidates = [self._create_candidate(i) for i in range(5)]

            # Mock DashScope 返回空结果
            with patch('dashscope.TextReRank.call') as mock_call:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.output = Mock()
                mock_response.output.results = []
                mock_call.return_value = mock_response

                outcome = reranker.rerank("测试问题", candidates)

                assert outcome.applied is False
                assert outcome.fallback_reason == "invalid_response"

    @staticmethod
    def _create_candidate(idx: int) -> RetrievalCandidate:
        """创建测试候选"""
        doc = Document(
            page_content=f"测试内容 {idx}",
            metadata={
                'document_version_id': 101,
                'chunk_index': idx,
                'source_name': f'test_{idx}.txt'
            }
        )
        cand = RetrievalCandidate(doc, f"101_{idx}")
        cand.rrf_score = 0.1 / (idx + 1)
        cand.initial_rrf_rank = idx + 1
        return cand


class TestRerankerSuccess:
    """测试 Reranker 成功执行"""

    def test_rerank_success_with_reordering(self):
        """测试：DashScope 返回乱序 index，候选按精排结果正确重排"""
        config = {'enabled': True, 'output_top_k': 3}

        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            reranker = Reranker(config)
            candidates = [self._create_candidate(i) for i in range(5)]

            # Mock DashScope 返回：索引 [4, 1, 0]（乱序）
            with patch('dashscope.TextReRank.call') as mock_call:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.output = Mock()
                mock_response.output.results = [
                    Mock(index=4, relevance_score=0.95),
                    Mock(index=1, relevance_score=0.88),
                    Mock(index=0, relevance_score=0.75)
                ]
                mock_call.return_value = mock_response

                outcome = reranker.rerank("测试问题", candidates)

                # 验证成功
                assert outcome.applied is True
                assert outcome.model == 'gte-rerank-v2'
                assert outcome.input_count == 5
                assert outcome.output_count == 3
                assert outcome.fallback_reason is None

                # 验证重排顺序：应为 [4, 1, 0]
                assert len(outcome.candidates) == 3
                assert outcome.candidates[0].document.metadata['chunk_index'] == 4
                assert outcome.candidates[1].document.metadata['chunk_index'] == 1
                assert outcome.candidates[2].document.metadata['chunk_index'] == 0

                # 验证 rerank 信息
                assert outcome.candidates[0].rerank_rank == 1
                assert outcome.candidates[0].rerank_score == 0.95
                assert outcome.candidates[1].rerank_rank == 2
                assert outcome.candidates[1].rerank_score == 0.88
                assert outcome.candidates[2].rerank_rank == 3
                assert outcome.candidates[2].rerank_score == 0.75

    def test_rerank_preserves_original_fields(self):
        """测试：精排后保留原始向量、BM25、RRF 字段"""
        config = {'enabled': True, 'output_top_k': 2}

        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            reranker = Reranker(config)

            # 创建带完整字段的候选
            candidates = []
            for i in range(3):
                doc = Document(
                    page_content=f"内容 {i}",
                    metadata={'document_version_id': 101, 'chunk_index': i}
                )
                cand = RetrievalCandidate(doc, f"101_{i}")
                cand.vector_distance = 0.2 + i * 0.1
                cand.vector_rank = i + 1
                cand.relevance_score = 0.9 - i * 0.1
                cand.bm25_score = 5.0 - i
                cand.bm25_rank = i + 1
                cand.rrf_score = 0.05 / (i + 1)
                cand.initial_rrf_rank = i + 1
                cand.retrieval_channels = ['vector', 'bm25']
                candidates.append(cand)

            # Mock DashScope 返回反向顺序
            with patch('dashscope.TextReRank.call') as mock_call:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.output = Mock()
                mock_response.output.results = [
                    Mock(index=2, relevance_score=0.92),
                    Mock(index=0, relevance_score=0.85)
                ]
                mock_call.return_value = mock_response

                outcome = reranker.rerank("测试", candidates)

                # 验证原始字段保留
                top1 = outcome.candidates[0]
                assert top1.vector_distance == 0.4  # 原索引 2
                assert top1.bm25_score == 3.0
                assert top1.initial_rrf_rank == 3
                assert top1.retrieval_channels == ['vector', 'bm25']

                # 验证新增 rerank 字段
                assert top1.rerank_rank == 1
                assert top1.rerank_score == 0.92

    def test_rerank_handles_duplicate_indices(self):
        """测试：DashScope 返回重复索引时跳过"""
        config = {'enabled': True, 'output_top_k': 3}

        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            reranker = Reranker(config)
            candidates = [self._create_candidate(i) for i in range(5)]

            # Mock DashScope 返回重复索引
            with patch('dashscope.TextReRank.call') as mock_call:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.output = Mock()
                mock_response.output.results = [
                    Mock(index=2, relevance_score=0.95),
                    Mock(index=2, relevance_score=0.90),  # 重复
                    Mock(index=1, relevance_score=0.85)
                ]
                mock_call.return_value = mock_response

                outcome = reranker.rerank("测试", candidates)

                # 应只有 2 个有效结果（跳过重复）
                assert outcome.applied is True
                assert outcome.output_count == 2
                assert outcome.candidates[0].document.metadata['chunk_index'] == 2
                assert outcome.candidates[1].document.metadata['chunk_index'] == 1

    def test_rerank_handles_out_of_bound_indices(self):
        """测试：DashScope 返回越界索引时跳过"""
        config = {'enabled': True, 'output_top_k': 3}

        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            reranker = Reranker(config)
            candidates = [self._create_candidate(i) for i in range(5)]  # 增加候选数

            # Mock DashScope 返回越界索引
            with patch('dashscope.TextReRank.call') as mock_call:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.output = Mock()
                mock_response.output.results = [
                    Mock(index=1, relevance_score=0.95),
                    Mock(index=99, relevance_score=0.90),  # 越界
                    Mock(index=0, relevance_score=0.85)
                ]
                mock_call.return_value = mock_response

                outcome = reranker.rerank("测试", candidates)

                # 应只有 2 个有效结果（跳过越界）
                assert outcome.applied is True
                assert outcome.output_count == 2
                assert outcome.candidates[0].document.metadata['chunk_index'] == 1
                assert outcome.candidates[1].document.metadata['chunk_index'] == 0

    @staticmethod
    def _create_candidate(idx: int) -> RetrievalCandidate:
        """创建测试候选"""
        doc = Document(
            page_content=f"测试内容 {idx}",
            metadata={
                'document_version_id': 101,
                'chunk_index': idx,
                'source_name': f'test_{idx}.txt'
            }
        )
        cand = RetrievalCandidate(doc, f"101_{idx}")
        cand.rrf_score = 0.1 / (idx + 1)
        cand.initial_rrf_rank = idx + 1
        return cand


class TestRerankerTextTruncation:
    """测试文本截断"""

    def test_query_truncation(self):
        """测试：超长问题被截断"""
        config = {'enabled': True, 'max_query_chars': 10}

        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            reranker = Reranker(config)
            candidates = [self._create_candidate(i) for i in range(5)]

            # Mock DashScope
            with patch('dashscope.TextReRank.call') as mock_call:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.output = Mock()
                mock_response.output.results = [
                    Mock(index=0, relevance_score=0.9)
                ]
                mock_call.return_value = mock_response

                long_query = "这是一个非常非常非常长的问题" * 10
                outcome = reranker.rerank(long_query, candidates, top_n=1)

                # 验证传给 DashScope 的问题被截断
                call_args = mock_call.call_args
                assert len(call_args.kwargs['query']) == 10

    def test_document_truncation(self):
        """测试：超长文档被截断"""
        config = {'enabled': True, 'max_document_chars': 20}

        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):
            reranker = Reranker(config)

            # 创建超长文档
            doc = Document(
                page_content="这是超长内容" * 100,
                metadata={'document_version_id': 101, 'chunk_index': 0}
            )
            cand = RetrievalCandidate(doc, "101_0")
            candidates = [cand] * 5

            # Mock DashScope
            with patch('dashscope.TextReRank.call') as mock_call:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.output = Mock()
                mock_response.output.results = [
                    Mock(index=0, relevance_score=0.9)
                ]
                mock_call.return_value = mock_response

                outcome = reranker.rerank("测试", candidates, top_n=1)

                # 验证传给 DashScope 的文档被截断
                call_args = mock_call.call_args
                for doc_text in call_args.kwargs['documents']:
                    assert len(doc_text) == 20

    @staticmethod
    def _create_candidate(idx: int) -> RetrievalCandidate:
        """创建测试候选"""
        doc = Document(
            page_content=f"测试内容 {idx}",
            metadata={
                'document_version_id': 101,
                'chunk_index': idx
            }
        )
        return RetrievalCandidate(doc, f"101_{idx}")
