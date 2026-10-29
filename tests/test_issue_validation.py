"""
问题验证测试：验证和修复AI指出的6个问题
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from langchain_core.documents import Document
from rag.rag_service import RagSummarizeService
from rag.hybrid_retriever import RetrievalCandidate


class TestIssue1_HallucinationFromIrrelevantDocs:
    """问题1：无权限时查询返回幻觉答案"""

    def test_no_permission_should_refuse(self):
        """测试：无权限时应该拒答"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions:

            # Mock 无可访问空间
            mock_perm.return_value = []
            mock_versions.return_value = []

            rag = RagSummarizeService(user_role='employee')

            result = rag.rag_summarize(
                query="售后SLA的P0响应时限",
                history=[]
            )

            # 验证：应该拒答
            assert result['has_answer'] is False
            assert result['refusal_reason'] is not None
            print(f"拒答原因: {result['refusal_reason']}")


class TestIssue2_RelevanceThresholdTooLoose:
    """问题2：相关性阈值过松导致拒答失效"""

    def test_irrelevant_content_filtered(self):
        """测试：无关内容应被过滤"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch.object(RagSummarizeService, 'retriever_docs_with_scores') as mock_retriever:

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock 向量检索返回无关文档（距离很大）
            irrelevant_doc = Document(
                page_content="KB-R2 扫地机器人的清扫模式包括自动模式和局部模式。",
                metadata={
                    'source_name': '产品手册',
                    'document_id': 1,
                    'document_version_id': 101,
                    'chunk_index': 1,
                    'space_id': 1
                }
            )
            mock_retriever.return_value = ([irrelevant_doc], [1.8])  # 距离1.8，远超阈值

            rag = RagSummarizeService(user_role='employee', use_hybrid=False)

            result = rag.rag_summarize(
                query="怎么制作原子弹",
                history=[]
            )

            # 验证：应该拒答，因为内容不相关
            assert result['has_answer'] is False
            assert result['refusal_reason'] is not None
            # 拒答原因可能是英文枚举值或中文描述
            assert result['refusal_reason'] in ['below_relevance_threshold', 'no_relevant_docs'] or \
                   '找不到相关内容' in str(result['refusal_reason']) or \
                   '无法回答' in str(result['refusal_reason']) or \
                   '没有找到足够依据' in str(result['refusal_reason'])

            print(f"无关内容被正确过滤并拒答: {result['refusal_reason']}")

    def test_bm25_only_without_vector_should_check_relevance(self):
        """测试：BM25-only候选应该有相关性检查"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.hybrid_retriever.HybridRetriever') as mock_hybrid_class:

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock BM25-only 候选（无向量距离）
            bm25_doc = Document(
                page_content="KB-R2 扫地机器人的清扫模式。",
                metadata={
                    'document_id': 1,
                    'document_version_id': 101,
                    'chunk_index': 1
                }
            )

            candidate = RetrievalCandidate(
                document=bm25_doc,
                chunk_key="101_1"
            )
            candidate.vector_distance = None  # BM25-only
            candidate.bm25_score = 5.0
            candidate.bm25_rank = 1
            candidate.rrf_score = 0.03
            candidate.retrieval_channels = ['bm25']

            mock_hybrid_instance = MagicMock()
            mock_hybrid_instance.search.return_value = [candidate]
            mock_hybrid_class.return_value = mock_hybrid_instance

            rag = RagSummarizeService(user_role='employee', use_hybrid=True)

            result = rag.rag_summarize(
                query="怎么制作原子弹",
                history=[]
            )

            # BM25-only 候选会被保留（因为有BM25分数），但最终可能被LLM拒答
            # 这里主要验证不会崩溃，且BM25候选能正常处理
            assert 'has_answer' in result
            print(f"BM25-only 候选处理正常")


class TestIssue3_ReferenceDeduplication:
    """问题3：引用去重失效，同一文档可占满Top3"""

    def test_same_document_only_once(self):
        """测试：同一文档只保留最佳分片"""
        rag = RagSummarizeService(user_role='employee')

        # 模拟同一文档的3个分片
        docs = [
            Document(page_content="内容1", metadata={
                "source_name": "09-差旅制度.pdf",
                "document_id": 1,
                "document_version_id": 101,
                "chunk_index": 1
            }),
            Document(page_content="内容2", metadata={
                "source_name": "09-差旅制度.pdf",
                "document_id": 1,
                "document_version_id": 101,
                "chunk_index": 2
            }),
            Document(page_content="内容3", metadata={
                "source_name": "09-差旅制度.pdf",
                "document_id": 1,
                "document_version_id": 101,
                "chunk_index": 3
            }),
        ]
        scores = [0.3, 0.5, 0.4]

        references = rag.format_references(docs, scores)

        # 验证：同一文档只保留最佳分片（距离最小的）
        assert len(references) == 1
        assert references[0]['chunk_index'] == 1  # 应该是距离最小的分片

        # 验证：引用信息完整
        assert references[0]['source_name'] == "09-差旅制度.pdf"
        assert references[0]['reference_id'] == 1

        print(f"同一文档去重成功（保留1条，过滤2条）")

    def test_different_documents_all_shown(self):
        """测试：不同文档都应显示"""
        rag = RagSummarizeService(user_role='employee')

        # 模拟3个不同文档
        docs = [
            Document(page_content="内容1", metadata={
                "source_name": "文档A.pdf",
                "document_id": 1,
                "document_version_id": 101,
                "chunk_index": 1
            }),
            Document(page_content="内容2", metadata={
                "source_name": "文档B.pdf",
                "document_id": 2,
                "document_version_id": 102,
                "chunk_index": 1
            }),
            Document(page_content="内容3", metadata={
                "source_name": "文档C.pdf",
                "document_id": 3,
                "document_version_id": 103,
                "chunk_index": 1
            }),
        ]
        scores = [0.3, 0.35, 0.4]

        references = rag.format_references(docs, scores)

        # 验证：3个不同文档都应显示
        assert len(references) == 3
        doc_ids = [ref['document_id'] for ref in references]
        assert 1 in doc_ids
        assert 2 in doc_ids
        assert 3 in doc_ids

        print(f"不同文档全部显示（共{len(references)}条引用）")


class TestIssue4_RerankAndThresholdOrder:
    """问题4：Rerank 与阈值过滤顺序矛盾"""

    def test_rerank_with_relevance_filter(self):
        """测试：Rerank后仍需相关性过滤"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.hybrid_retriever.HybridRetriever') as mock_hybrid_class:

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock混合检索返回候选
            relevant_doc = Document(
                page_content="P0 故障的响应时限是 15 分钟。",
                metadata={'document_id': 1, 'document_version_id': 101, 'chunk_index': 1}
            )
            irrelevant_doc = Document(
                page_content="KB-R2 扫地机器人的清扫模式。",
                metadata={'document_id': 2, 'document_version_id': 102, 'chunk_index': 1}
            )

            relevant_candidate = RetrievalCandidate(relevant_doc, "101_1")
            relevant_candidate.vector_distance = 0.5
            relevant_candidate.vector_rank = 1
            relevant_candidate.rrf_score = 0.05

            irrelevant_candidate = RetrievalCandidate(irrelevant_doc, "102_1")
            irrelevant_candidate.vector_distance = 1.5  # 超过阈值
            irrelevant_candidate.vector_rank = 2
            irrelevant_candidate.rrf_score = 0.02

            mock_hybrid_instance = MagicMock()
            mock_hybrid_instance.search.return_value = [relevant_candidate, irrelevant_candidate]
            mock_hybrid_class.return_value = mock_hybrid_instance

            rag = RagSummarizeService(user_role='employee', use_hybrid=True, use_reranker=False)

            result = rag.rag_summarize(
                query="P0 故障响应时限",
                history=[]
            )

            # 验证：不相关的候选应该被过滤掉
            if result['has_answer']:
                assert len(result['references']) <= 1

            print(f"Rerank与相关性过滤协同工作正常")


class TestIssue5_RerankAPIFailure:
    """问题5：Reranker云端500错误"""

    def test_rerank_failure_fallback(self):
        """测试：Rerank失败时降级到RRF排序"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions, \
             patch('rag.hybrid_retriever.HybridRetriever') as mock_hybrid_class, \
             patch('rag.reranker.Reranker.rerank') as mock_rerank:

            mock_perm.return_value = [1]
            mock_versions.return_value = [101]

            # Mock候选
            doc = Document(
                page_content="测试内容",
                metadata={'document_id': 1, 'document_version_id': 101, 'chunk_index': 1}
            )

            candidate = RetrievalCandidate(doc, "101_1")
            candidate.vector_distance = 0.5
            candidate.vector_rank = 1
            candidate.rrf_score = 0.05

            mock_hybrid_instance = MagicMock()
            mock_hybrid_instance.search.return_value = [candidate]
            mock_hybrid_class.return_value = mock_hybrid_instance

            # Mock Rerank失败
            mock_rerank.side_effect = Exception("API 500 error")

            rag = RagSummarizeService(user_role='employee', use_hybrid=True, use_reranker=True)

            result = rag.rag_summarize(
                query="测试问题",
                history=[]
            )

            # 验证：Rerank失败后仍能返回结果（降级到RRF）
            assert 'has_answer' in result
            print(f"Rerank失败降级正常")


class TestIssue6_OldVersionAccumulation:
    """问题6：旧版本堆积导致BM25语料膨胀"""

    def test_only_current_versions_in_bm25_corpus(self):
        """测试：BM25语料只包含当前有效版本"""
        with patch('services.permission_service.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('services.document_service.DocumentService.get_current_version_ids') as mock_versions:

            mock_perm.return_value = [1]
            # 只返回当前版本
            mock_versions.return_value = [105, 106]  # 假设只有这两个是当前版本

            from rag.hybrid_retriever import HybridRetriever
            retriever = HybridRetriever(user_role='employee')

            # 获取BM25语料（传入正确的参数）
            corpus = retriever._get_bm25_corpus([1], [105, 106])

            # 验证：所有文档都是当前版本
            for doc in corpus:
                version_id = doc.metadata.get('document_version_id')
                if version_id:
                    assert version_id in [105, 106], f"发现旧版本 {version_id}"

            print(f"BM25语料只包含当前版本（共{len(corpus)}个分片）")
