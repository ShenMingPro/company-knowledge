"""
Query 改写与 RAG 集成测试

测试 Query 改写模块与现有 RAG 服务的集成
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from rag.query_rewriter import QueryRewriter, RewriteResult
from rag.rag_service import RagSummarizeService


class TestQueryRewriterRagIntegration:
    """Query 改写与 RAG 服务集成测试"""

    def test_rewrite_used_for_both_vector_and_bm25(self):
        """测试：改写后的问题同时用于向量检索和 BM25 检索"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class:

            # Mock 权限和版本
            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock 混合检索实例 - 返回有结果，避免触发回退
            mock_hybrid_instance = MagicMock()
            mock_stats = {'vector_count': 1, 'bm25_count': 0, 'fused_count': 1}
            mock_candidate = MagicMock()
            mock_candidate.document.page_content = "KB-R2 支持自动回充"
            mock_candidate.document.metadata = {"source_name": "test.pdf"}
            mock_hybrid_instance.search.return_value = ([mock_candidate], mock_stats)
            mock_hybrid_class.return_value = mock_hybrid_instance

            # 创建 RAG 服务
            rag = RagSummarizeService(user_role='employee', use_hybrid=True)

            # Mock query rewriter
            mock_rewriter = Mock(spec=QueryRewriter)
            mock_rewriter.rewrite.return_value = RewriteResult(
                original_query="那它支持自动回充吗？",
                rewritten_query="KB-R2 是否支持自动回充？",
                applied=True,
                fallback_reason=None,
                rewrite_time_ms=50
            )
            rag.query_rewriter = mock_rewriter

            # 执行查询
            history = [
                {"role": "user", "content": "KB-R2 支持哪些清扫模式？"},
                {"role": "assistant", "content": "KB-R2 支持自动模式、局部清扫模式。"}
            ]

            result = rag.rag_summarize(
                query="那它支持自动回充吗？",
                history=history
            )

            # 验证 query rewriter 被调用
            mock_rewriter.rewrite.assert_called_once()

            # 验证混合检索使用改写后的问题（只调用一次，不触发回退）
            mock_hybrid_instance.search.assert_called_once()
            call_args = mock_hybrid_instance.search.call_args
            assert call_args[0][0] == "KB-R2 是否支持自动回充？"  # retrieval_query

            print("✅ 改写后的问题同时用于向量检索和 BM25 检索")

    def test_original_query_in_result(self):
        """测试：结果中包含原始问题和改写问题"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class:

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            mock_hybrid_instance = MagicMock()
            mock_stats = {'vector_count': 0, 'bm25_count': 0, 'fused_count': 0}
            mock_hybrid_instance.search.return_value = ([], mock_stats)
            mock_hybrid_class.return_value = mock_hybrid_instance

            rag = RagSummarizeService(user_role='employee', use_hybrid=True)

            # Mock query rewriter
            mock_rewriter = Mock(spec=QueryRewriter)
            mock_rewriter.rewrite.return_value = RewriteResult(
                original_query="那它支持自动回充吗？",
                rewritten_query="KB-R2 是否支持自动回充？",
                applied=True,
                fallback_reason=None,
                rewrite_time_ms=50
            )
            rag.query_rewriter = mock_rewriter

            history = [{"role": "user", "content": "KB-R2 的功能"}]

            result = rag.rag_summarize(
                query="那它支持自动回充吗？",
                history=history
            )

            # 验证结果包含原始问题和改写问题
            assert result['original_query'] == "那它支持自动回充吗？"
            assert result['retrieval_query'] == "KB-R2 是否支持自动回充？"

            print("✅ 结果中包含原始问题和改写问题")

    def test_rewrite_fallback_on_no_results(self):
        """测试：改写后无结果时使用原始问题重试"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class:

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock 混合检索：第一次无结果，第二次有结果
            mock_hybrid_instance = MagicMock()
            mock_stats = {'vector_count': 0, 'bm25_count': 0, 'fused_count': 0}
            mock_hybrid_instance.search.side_effect = [
                ([], mock_stats),  # 第一次空
                ([MagicMock()], {'vector_count': 1, 'bm25_count': 0, 'fused_count': 1})  # 第二次有结果
            ]
            mock_hybrid_class.return_value = mock_hybrid_instance

            rag = RagSummarizeService(user_role='employee', use_hybrid=True)

            # Mock query rewriter
            mock_rewriter = Mock(spec=QueryRewriter)
            mock_rewriter.rewrite.return_value = RewriteResult(
                original_query="那周末呢？",
                rewritten_query="员工周末加班是否需要提前申请？",
                applied=True,
                fallback_reason=None,
                rewrite_time_ms=50
            )
            rag.query_rewriter = mock_rewriter

            history = [{"role": "user", "content": "员工加班需要提前申请吗？"}]

            result = rag.rag_summarize(
                query="那周末呢？",
                history=history
            )

            # 验证混合检索被调用两次
            assert mock_hybrid_instance.search.call_count == 2

            # 第一次使用改写后的问题
            first_call = mock_hybrid_instance.search.call_args_list[0]
            assert first_call[0][0] == "员工周末加班是否需要提前申请？"

            # 第二次使用原始问题
            second_call = mock_hybrid_instance.search.call_args_list[1]
            assert second_call[0][0] == "那周末呢？"

            print("✅ 改写后无结果时使用原始问题重试")

    def test_query_rewrite_disabled_uses_original(self):
        """测试：Query 改写禁用时直接使用原始问题"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class:

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            mock_hybrid_instance = MagicMock()
            mock_stats = {'vector_count': 0, 'bm25_count': 0, 'fused_count': 0}
            mock_hybrid_instance.search.return_value = ([], mock_stats)
            mock_hybrid_class.return_value = mock_hybrid_instance

            # 禁用 Query 改写
            rag = RagSummarizeService(user_role='employee', use_hybrid=True, use_query_rewrite=False)

            history = [{"role": "user", "content": "KB-R2 的功能"}]

            result = rag.rag_summarize(
                query="那它支持自动回充吗？",
                history=history
            )

            # 验证直接使用原始问题检索
            mock_hybrid_instance.search.assert_called_once()
            call_args = mock_hybrid_instance.search.call_args
            assert call_args[0][0] == "那它支持自动回充吗？"

            print("✅ Query 改写禁用时直接使用原始问题")

    def test_rewrite_failure_does_not_break_rag(self):
        """测试：Query 改写失败不影响 RAG 流程"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class:

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            mock_hybrid_instance = MagicMock()
            mock_stats = {'vector_count': 0, 'bm25_count': 0, 'fused_count': 0}
            mock_hybrid_instance.search.return_value = ([], mock_stats)
            mock_hybrid_class.return_value = mock_hybrid_instance

            rag = RagSummarizeService(user_role='employee', use_hybrid=True)

            # Mock query rewriter 返回错误结果
            mock_rewriter = Mock(spec=QueryRewriter)
            mock_rewriter.rewrite.return_value = RewriteResult(
                original_query="那它呢？",
                rewritten_query="那它呢？",  # 改写失败，返回原问题
                applied=False,
                fallback_reason="error: 模型调用失败",
                rewrite_time_ms=0
            )
            rag.query_rewriter = mock_rewriter

            history = [{"role": "user", "content": "测试"}]

            # 执行不应该抛出异常
            try:
                result = rag.rag_summarize(
                    query="那它呢？",
                    history=history
                )
                # 应该使用原始问题继续检索
                mock_hybrid_instance.search.assert_called_once()
                print("✅ Query 改写失败不影响 RAG 流程")
            except Exception as e:
                pytest.fail(f"Query 改写失败不应该中断 RAG 流程: {e}")


class TestQueryRewriterResultFields:
    """Query 改写结果字段测试"""

    def test_result_contains_rewrite_fields(self):
        """测试：RAG 结果包含 Query 改写相关字段"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.rag_service.HybridRetriever') as mock_hybrid_class:

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # 返回有结果，避免触发回退
            mock_hybrid_instance = MagicMock()
            mock_stats = {'vector_count': 1, 'bm25_count': 0, 'fused_count': 1}
            mock_candidate = MagicMock()
            mock_candidate.document.page_content = "KB-R2 支持自动回充"
            mock_candidate.document.metadata = {"source_name": "test.pdf"}
            mock_hybrid_instance.search.return_value = ([mock_candidate], mock_stats)
            mock_hybrid_class.return_value = mock_hybrid_instance

            rag = RagSummarizeService(user_role='employee', use_hybrid=True)

            # Mock query rewriter
            mock_rewriter = Mock(spec=QueryRewriter)
            mock_rewriter.rewrite.return_value = RewriteResult(
                original_query="那它呢？",
                rewritten_query="KB-R2 是否支持自动回充？",
                applied=True,
                fallback_reason=None,
                rewrite_time_ms=50
            )
            rag.query_rewriter = mock_rewriter

            history = [{"role": "user", "content": "KB-R2 的功能"}]

            result = rag.rag_summarize(
                query="那它呢？",
                history=history
            )

            # 验证结果包含 Query 改写字段
            assert 'original_query' in result
            assert 'retrieval_query' in result
            assert 'query_rewrite_applied' in result
            assert 'query_rewrite_time_ms' in result

            assert result['original_query'] == "那它呢？"
            assert result['retrieval_query'] == "KB-R2 是否支持自动回充？"
            assert result['query_rewrite_applied'] is True
            assert result['query_rewrite_time_ms'] == 50

            print("✅ RAG 结果包含 Query 改写相关字段")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
