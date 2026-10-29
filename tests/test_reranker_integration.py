"""
Reranker 集成测试
测试 Reranker 与 RAG 服务的集成（使用 Mock，不产生真实 API 调用）
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from langchain_core.documents import Document
from rag.rag_service import RagSummarizeService
from rag.hybrid_retriever import RetrievalCandidate


class TestRerankerRagIntegration:
    """Reranker 与 RAG 服务集成测试"""

    def test_reranker_enabled_by_default_in_hybrid_mode(self):
        """测试：混合检索启用时，Reranker 默认启用"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm:
            mock_perm.return_value = [1]

            rag = RagSummarizeService(user_role='employee', use_hybrid=True)

            # 验证 Reranker 已初始化
            assert rag.use_reranker is True
            assert rag.reranker is not None

    def test_reranker_disabled_in_vector_only_mode(self):
        """测试：纯向量检索模式下，Reranker 不生效"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm:
            mock_perm.return_value = [1]

            rag = RagSummarizeService(user_role='employee', use_hybrid=False, use_reranker=True)

            # Reranker 标志为 True，但实例为 None（因为混合检索未启用）
            assert rag.use_reranker is True
            assert rag.reranker is None

    def test_reranker_can_be_explicitly_disabled(self):
        """测试：Reranker 可以显式禁用"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm:
            mock_perm.return_value = [1]

            rag = RagSummarizeService(user_role='employee', use_hybrid=True, use_reranker=False)

            assert rag.use_reranker is False
            assert rag.reranker is None

    def test_hybrid_retrieves_top10_when_reranker_enabled(self):
        """测试：Reranker 启用时，混合检索返回 Top 10"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class, \
             patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock 混合检索返回 10 个候选
            candidates = [self._create_candidate(i) for i in range(10)]
            mock_hybrid_instance = MagicMock()
            mock_hybrid_instance.search.return_value = (candidates, {'retrieval_mode': 'hybrid'})
            mock_hybrid_class.return_value = mock_hybrid_instance

            # Mock Reranker 返回 Top 3
            with patch('rag.reranker.Reranker.rerank') as mock_rerank:
                from rag.reranker import RerankOutcome
                mock_rerank.return_value = RerankOutcome(
                    candidates=candidates[:3],
                    applied=True,
                    model='gte-rerank-v2',
                    input_count=10,
                    output_count=3,
                    rerank_time_ms=50,
                    fallback_reason=None
                )

                rag = RagSummarizeService(user_role='employee', use_hybrid=True, use_reranker=True)

                # Mock 答案生成
                with patch.object(rag, 'chain') as mock_chain:
                    mock_chain.invoke.return_value = "测试答案"

                    result = rag.rag_summarize("测试问题")

                    # 验证混合检索被调用且请求 Top 10
                    mock_hybrid_instance.search.assert_called_once()
                    call_args = mock_hybrid_instance.search.call_args
                    assert call_args[1]['top_k'] == 10

                    # 验证 Reranker 被调用
                    mock_rerank.assert_called_once()

                    # 验证最终结果
                    assert result['rerank_applied'] is True
                    assert result['rerank_candidate_count'] == 10
                    assert result['rerank_output_count'] == 3

    def test_reranker_uses_retrieval_query_not_original(self):
        """测试：Reranker 使用实际检索问题（Query 改写后的），不是原始问题"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class, \
             patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock 混合检索
            candidates = [self._create_candidate(i) for i in range(10)]
            mock_hybrid_instance = MagicMock()
            mock_hybrid_instance.search.return_value = (candidates, {'retrieval_mode': 'hybrid'})
            mock_hybrid_class.return_value = mock_hybrid_instance

            rag = RagSummarizeService(user_role='employee', use_hybrid=True, use_query_rewrite=True, use_reranker=True)

            # Mock Query 改写
            from rag.query_rewriter import RewriteResult
            mock_rewriter = Mock()
            mock_rewriter.rewrite.return_value = RewriteResult(
                original_query="它支持自动回充吗？",
                rewritten_query="KB-R2 是否支持自动回充功能？",
                applied=True,
                fallback_reason=None,
                rewrite_time_ms=30
            )
            rag.query_rewriter = mock_rewriter

            # Mock Reranker
            with patch('rag.reranker.Reranker.rerank') as mock_rerank:
                from rag.reranker import RerankOutcome
                mock_rerank.return_value = RerankOutcome(
                    candidates=candidates[:3],
                    applied=True,
                    model='gte-rerank-v2',
                    input_count=10,
                    output_count=3,
                    rerank_time_ms=50,
                    fallback_reason=None
                )

                # Mock 答案生成
                with patch.object(rag, 'chain') as mock_chain:
                    mock_chain.invoke.return_value = "支持自动回充"

                    result = rag.rag_summarize(
                        query="它支持自动回充吗？",
                        history=[{"role": "user", "content": "KB-R2 的功能"}]
                    )

                    # 验证 Reranker 使用改写后的问题
                    mock_rerank.assert_called_once()
                    rerank_call_args = mock_rerank.call_args
                    assert rerank_call_args[1]['query'] == "KB-R2 是否支持自动回充功能？"

                    # 验证答案生成使用原始问题
                    mock_chain.invoke.assert_called_once()
                    chain_call_args = mock_chain.invoke.call_args[0][0]
                    assert chain_call_args['input'] == "它支持自动回充吗？"

    def test_reranker_reorders_candidates_correctly(self):
        """测试：Reranker 正确重排候选并保持字段对齐"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class, \
             patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock 混合检索：初始 RRF 排序为 [0, 1, 2, 3, 4]
            candidates = []
            for i in range(5):
                doc = Document(
                    page_content=f"内容 {i}",
                    metadata={'document_version_id': 101, 'chunk_index': i, 'source_name': f'test_{i}.txt'}
                )
                cand = RetrievalCandidate(doc, f"101_{i}")
                cand.vector_distance = 0.5 + i * 0.1
                cand.vector_rank = i + 1
                cand.relevance_score = 0.8 - i * 0.1
                cand.bm25_score = 10 - i
                cand.bm25_rank = i + 1
                cand.rrf_score = 0.1 / (i + 1)
                cand.initial_rrf_rank = i + 1
                cand.retrieval_channels = ['vector', 'bm25']
                candidates.append(cand)

            mock_hybrid_instance = MagicMock()
            mock_hybrid_instance.search.return_value = (candidates, {'retrieval_mode': 'hybrid'})
            mock_hybrid_class.return_value = mock_hybrid_instance

            # Mock Reranker：反向排序 [4, 2, 0]
            with patch('rag.reranker.Reranker.rerank') as mock_rerank:
                reranked = [candidates[4], candidates[2], candidates[0]]
                for rank, cand in enumerate(reranked, 1):
                    cand.rerank_rank = rank
                    cand.rerank_score = 0.95 - rank * 0.05

                from rag.reranker import RerankOutcome
                mock_rerank.return_value = RerankOutcome(
                    candidates=reranked,
                    applied=True,
                    model='gte-rerank-v2',
                    input_count=5,
                    output_count=3,
                    rerank_time_ms=50,
                    fallback_reason=None
                )

                rag = RagSummarizeService(user_role='employee', use_hybrid=True, use_reranker=True)

                # Mock 答案生成
                with patch.object(rag, 'chain') as mock_chain:
                    mock_chain.invoke.return_value = "测试答案"

                    result = rag.rag_summarize("测试")

                    # 验证引用顺序：应为 [4, 2, 0]
                    assert len(result['references']) == 3
                    assert result['references'][0]['chunk_index'] == 4
                    assert result['references'][1]['chunk_index'] == 2
                    assert result['references'][2]['chunk_index'] == 0

                    # 验证引用包含 rerank 信息
                    assert result['references'][0]['rerank_rank'] == 1
                    assert result['references'][0]['rerank_score'] == 0.90
                    assert result['references'][1]['rerank_rank'] == 2
                    assert result['references'][2]['rerank_rank'] == 3

                    # 验证引用保留原始 RRF 排名
                    assert result['references'][0]['initial_rrf_rank'] == 5
                    assert result['references'][1]['initial_rrf_rank'] == 3
                    assert result['references'][2]['initial_rrf_rank'] == 1

    def test_reranker_fallback_preserves_rrf_order(self):
        """测试：Reranker 失败时保留 RRF Top 3 顺序"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class, \
             patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock 混合检索返回 10 个候选
            candidates = [self._create_candidate(i) for i in range(10)]
            mock_hybrid_instance = MagicMock()
            mock_hybrid_instance.search.return_value = (candidates, {'retrieval_mode': 'hybrid'})
            mock_hybrid_class.return_value = mock_hybrid_instance

            # Mock Reranker 失败
            with patch('rag.reranker.Reranker.rerank') as mock_rerank:
                from rag.reranker import RerankOutcome
                mock_rerank.return_value = RerankOutcome(
                    candidates=candidates[:3],  # 降级返回 Top 3
                    applied=False,
                    model=None,
                    input_count=10,
                    output_count=3,
                    rerank_time_ms=5,
                    fallback_reason='api_error'
                )

                rag = RagSummarizeService(user_role='employee', use_hybrid=True, use_reranker=True)

                # Mock 答案生成
                with patch.object(rag, 'chain') as mock_chain:
                    mock_chain.invoke.return_value = "测试答案"

                    result = rag.rag_summarize("测试")

                    # 验证降级信息
                    assert result['rerank_applied'] is False
                    assert result['rerank_fallback_reason'] == 'api_error'

                    # 验证仍有答案（不影响问答）
                    assert result['has_answer'] is True
                    assert result['answer'] == "测试答案"

                    # 验证引用顺序为 RRF Top 3
                    assert len(result['references']) == 3
                    assert result['references'][0]['chunk_index'] == 0
                    assert result['references'][1]['chunk_index'] == 1
                    assert result['references'][2]['chunk_index'] == 2

    def test_reranker_skipped_when_candidates_insufficient(self):
        """测试：候选数不足 3 时跳过 Reranker"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class, \
             patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'test-key'}):

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock 混合检索只返回 2 个候选
            candidates = [self._create_candidate(i) for i in range(2)]
            mock_hybrid_instance = MagicMock()
            mock_hybrid_instance.search.return_value = (candidates, {'retrieval_mode': 'hybrid'})
            mock_hybrid_class.return_value = mock_hybrid_instance

            rag = RagSummarizeService(user_role='employee', use_hybrid=True, use_reranker=True)

            # Mock Reranker（应不被调用）
            with patch('rag.reranker.Reranker.rerank') as mock_rerank:
                from rag.reranker import RerankOutcome
                mock_rerank.return_value = RerankOutcome(
                    candidates=candidates,
                    applied=False,
                    model=None,
                    input_count=2,
                    output_count=2,
                    rerank_time_ms=0,
                    fallback_reason='insufficient_candidates'
                )

                # Mock 答案生成
                with patch.object(rag, 'chain') as mock_chain:
                    mock_chain.invoke.return_value = "测试答案"

                    result = rag.rag_summarize("测试")

                    # 验证 Reranker 被调用但未应用
                    assert result['rerank_applied'] is False
                    assert result['rerank_fallback_reason'] == 'insufficient_candidates'

    def test_no_reranker_when_no_api_key(self):
        """测试：无 API Key 时 Reranker 降级"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class, \
             patch.dict('os.environ', {}, clear=True):  # 清空环境变量

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            candidates = [self._create_candidate(i) for i in range(10)]
            mock_hybrid_instance = MagicMock()
            mock_hybrid_instance.search.return_value = (candidates, {'retrieval_mode': 'hybrid'})
            mock_hybrid_class.return_value = mock_hybrid_instance

            rag = RagSummarizeService(user_role='employee', use_hybrid=True, use_reranker=True)

            # Mock 答案生成
            with patch.object(rag, 'chain') as mock_chain:
                mock_chain.invoke.return_value = "测试答案"

                result = rag.rag_summarize("测试")

                # 验证降级
                assert result['rerank_applied'] is False
                assert result['rerank_fallback_reason'] == 'missing_api_key'

                # 验证仍能正常回答
                assert result['has_answer'] is True

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
        cand.vector_distance = 0.5 + idx * 0.05
        cand.vector_rank = idx + 1
        cand.relevance_score = 0.9 - idx * 0.05
        cand.bm25_score = 10 - idx
        cand.bm25_rank = idx + 1
        cand.rrf_score = 0.1 / (idx + 1)
        cand.initial_rrf_rank = idx + 1
        cand.retrieval_channels = ['vector', 'bm25']
        return cand
