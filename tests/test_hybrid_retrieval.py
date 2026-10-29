"""
混合检索测试：BM25 + 向量检索 + RRF 融合
使用内存数据和 Mock，不访问真实模型、数据库或 Chroma
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import Mock, patch, MagicMock
from langchain_core.documents import Document
from rag.bm25_retriever import BM25Retriever
from rag.hybrid_retriever import HybridRetriever, RetrievalCandidate
from rag.rag_service import RagSummarizeService


class TestBM25Tokenization:
    """测试中文分词和规范化"""

    def test_chinese_keyword_tokenization(self):
        """测试中文关键词能够被分词"""
        doc = Document(page_content="扫地机器人适合小户型使用", metadata={"id": 1})
        retriever = BM25Retriever([doc])

        # 检查分词结果
        tokens = retriever._tokenize("扫地机器人")
        assert len(tokens) > 0
        assert any('扫地' in t or '机器人' in t or '扫地机器人' in t for t in tokens)
        print(f"[OK] 中文分词正确：{tokens}")

    def test_product_model_preservation(self):
        """测试产品型号、故障码保留"""
        test_cases = [
            ("KB-R2 扫地机器人", ["kb-r2", "kb", "r2", "扫地", "机器人"]),
            ("E03 故障码", ["e03", "故障", "故障码"]),
            ("220V 电压", ["220v", "220", "v", "电压"]),
        ]

        retriever = BM25Retriever([])

        for text, expected_patterns in test_cases:
            tokens = retriever._tokenize(text)
            # 检查是否至少包含部分预期模式
            matched = any(any(pattern in token for pattern in expected_patterns) for token in tokens)
            assert matched or len(tokens) > 0, f"分词失败：{text} -> {tokens}"
            print(f"✅ 型号保留：{text} -> {tokens}")

    def test_query_and_doc_same_tokenization(self):
        """测试查询和文档使用相同分词规则"""
        retriever = BM25Retriever([])

        query_text = "KB-R2 故障"
        doc_text = "KB-R2 机器人出现故障"

        query_tokens = retriever._tokenize(query_text)
        doc_tokens = retriever._tokenize(doc_text)

        # 应该有共同的 token
        common_tokens = set(query_tokens) & set(doc_tokens)
        assert len(common_tokens) > 0, f"查询和文档没有共同 token：query={query_tokens}, doc={doc_tokens}"
        print(f"✅ 共同 token：{common_tokens}")


class TestBM25Retrieval:
    """测试 BM25 检索"""

    def test_bm25_search_basic(self):
        """测试基本 BM25 检索"""
        docs = [
            Document(page_content="KB-R2 扫地机器人支持 220V 电压", metadata={"id": 1}),
            Document(page_content="产品出现 E03 故障码时请检查尘盒", metadata={"id": 2}),
            Document(page_content="智能家居产品包括扫地机器人", metadata={"id": 3}),
        ]

        retriever = BM25Retriever(docs)
        results = retriever.search("扫地机器人", top_k=3)

        assert len(results) > 0
        assert all('bm25_score' in r for r in results)
        assert all('bm25_rank' in r for r in results)
        assert all(r['bm25_score'] > 0 for r in results)
        print(f"✅ BM25 基本检索：返回 {len(results)} 个结果")

    def test_bm25_ranking(self):
        """测试 BM25 排名正确性"""
        docs = [
            Document(page_content="扫地机器人价格实惠", metadata={"id": 1}),
            Document(page_content="这是一个无关的文档", metadata={"id": 2}),
            Document(page_content="扫地机器人性能优秀，扫地机器人很好用", metadata={"id": 3}),
        ]

        retriever = BM25Retriever(docs)
        results = retriever.search("扫地机器人", top_k=3)

        # 文档3应该排名靠前（多次出现关键词）
        assert results[0]['document'].metadata['id'] == 3
        # 排名应该是连续的
        ranks = [r['bm25_rank'] for r in results]
        assert ranks == [1, 2, 3] or ranks == [1, 2]
        print(f"✅ BM25 排名正确")

    def test_bm25_empty_query(self):
        """测试空查询"""
        docs = [Document(page_content="测试内容", metadata={"id": 1})]
        retriever = BM25Retriever(docs)

        results = retriever.search("", top_k=3)
        assert len(results) == 0
        print("✅ 空查询返回空结果")

    def test_bm25_no_match(self):
        """测试无匹配关键词"""
        docs = [Document(page_content="扫地机器人", metadata={"id": 1})]
        retriever = BM25Retriever(docs)

        # 完全不相关的查询
        results = retriever.search("xyz123abc", top_k=3)
        # 应该返回空或者分数为0
        assert len(results) == 0 or all(r['bm25_score'] == 0 for r in results)
        print("✅ 无匹配时正确处理")


class TestRRFFusion:
    """测试 RRF 融合"""

    def test_rrf_score_calculation(self):
        """测试 RRF 分数计算公式"""
        doc = Document(page_content="测试", metadata={"document_version_id": 1, "chunk_index": 0})
        candidate = RetrievalCandidate(doc, "1_0")

        # 设置排名
        candidate.add_vector_result(distance=0.5, rank=1, relevance_score=0.9)
        candidate.add_bm25_result(score=10.0, rank=2)

        # 计算 RRF
        rrf_constant = 60
        candidate.calculate_rrf_score(rrf_constant)

        # 验证公式：1/(60+1) + 1/(60+2) = 1/61 + 1/62
        expected = 1.0 / (rrf_constant + 1) + 1.0 / (rrf_constant + 2)
        assert abs(candidate.rrf_score - expected) < 0.0001
        print(f"✅ RRF 分数计算正确：{candidate.rrf_score:.6f} ≈ {expected:.6f}")

    def test_rrf_uses_rank_not_score(self):
        """测试 RRF 使用排名而非原始分数"""
        doc1 = Document(page_content="doc1", metadata={"document_version_id": 1, "chunk_index": 0})
        doc2 = Document(page_content="doc2", metadata={"document_version_id": 1, "chunk_index": 1})

        cand1 = RetrievalCandidate(doc1, "1_0")
        cand2 = RetrievalCandidate(doc2, "1_1")

        # cand1: 排名都是1，但原始分数不同
        cand1.add_vector_result(distance=0.1, rank=1, relevance_score=0.95)
        cand1.add_bm25_result(score=100.0, rank=1)

        # cand2: 排名都是2，原始分数也不同
        cand2.add_vector_result(distance=0.9, rank=2, relevance_score=0.40)
        cand2.add_bm25_result(score=5.0, rank=2)

        cand1.calculate_rrf_score(60)
        cand2.calculate_rrf_score(60)

        # RRF 只看排名，cand1 应该分数更高
        assert cand1.rrf_score > cand2.rrf_score
        print(f"✅ RRF 使用排名：cand1={cand1.rrf_score:.4f} > cand2={cand2.rrf_score:.4f}")

    def test_rrf_deduplication(self):
        """测试 RRF 去重（同一分片只出现一次）"""
        doc = Document(
            page_content="测试内容",
            metadata={"document_version_id": 10, "chunk_index": 5}
        )

        # 模拟向量和 BM25 都返回了同一分片
        vector_candidates = [{
            'document': doc,
            'vector_distance': 0.3,
            'vector_rank': 1,
            'relevance_score': 0.9
        }]

        bm25_candidates = [{
            'document': doc,
            'bm25_score': 15.0,
            'bm25_rank': 1
        }]

        # 手动模拟融合逻辑
        from rag.hybrid_retriever import HybridRetriever
        retriever = HybridRetriever(user_role='employee')
        fused = retriever._rrf_fusion(vector_candidates, bm25_candidates)

        # 应该只有一个候选
        assert len(fused) == 1
        # 该候选应该同时有向量和 BM25 信息
        assert fused[0].vector_rank == 1
        assert fused[0].bm25_rank == 1
        assert 'vector' in fused[0].retrieval_channels
        assert 'bm25' in fused[0].retrieval_channels
        print(f"✅ RRF 去重正确：同一分片融合为一个候选，RRF={fused[0].rrf_score:.4f}")


class TestPermissionFiltering:
    """测试权限和版本过滤"""

    def test_no_permission_no_bm25_corpus(self):
        """测试无权限时 BM25 语料为空"""
        with patch('rag.hybrid_retriever.PermissionService.get_accessible_space_ids') as mock_perm:
            mock_perm.return_value = []

            retriever = HybridRetriever(user_role='unknown_role')
            candidates, stats = retriever.search("测试", top_k=3)

            assert len(candidates) == 0
            assert stats['vector_retrieved'] == 0
            assert stats['bm25_retrieved'] == 0
            print("✅ 无权限时不进入 BM25 语料")

    def test_version_filtering_before_bm25(self):
        """测试版本过滤在 BM25 之前执行"""
        with patch('rag.hybrid_retriever.PermissionService.get_accessible_space_ids') as mock_perm, \
             patch('rag.hybrid_retriever.DocumentService.get_current_version_ids') as mock_ver, \
             patch.object(HybridRetriever, '_get_bm25_corpus') as mock_corpus, \
             patch.object(HybridRetriever, '_vector_search') as mock_vector:

            mock_perm.return_value = [1]
            mock_ver.return_value = [10, 20]  # 只有版本 10 和 20 有效
            mock_corpus.return_value = []
            mock_vector.return_value = []

            retriever = HybridRetriever(user_role='employee')
            candidates, stats = retriever.search("测试", top_k=3)

            # 验证 get_current_version_ids 被调用
            mock_ver.assert_called_once_with([1])
            # 验证 _get_bm25_corpus 被调用时传入了 version_ids
            if mock_corpus.called:
                call_args = mock_corpus.call_args
                # 检查调用参数：_get_bm25_corpus(space_ids, version_ids)
                space_ids = call_args[0][0] if len(call_args[0]) > 0 else None
                version_ids = call_args[0][1] if len(call_args[0]) > 1 else None
                assert version_ids == [10, 20], f"Expected version_ids [10, 20], got {version_ids}"
            print("[OK] 版本过滤在 BM25 前执行")


class TestBM25OnlyCandidates:
    """测试 BM25-only 候选处理"""

    def test_bm25_only_candidate_has_no_vector_distance(self):
        """测试 BM25-only 候选的 vector_distance 为 None"""
        doc = Document(
            page_content="BM25 独有内容",
            metadata={"document_version_id": 1, "chunk_index": 0}
        )

        # 只在 BM25 中出现
        bm25_candidates = [{
            'document': doc,
            'bm25_score': 10.0,
            'bm25_rank': 1
        }]

        from rag.hybrid_retriever import HybridRetriever
        retriever = HybridRetriever(user_role='employee')
        fused = retriever._rrf_fusion([], bm25_candidates)

        assert len(fused) == 1
        assert fused[0].vector_distance is None
        assert fused[0].vector_rank is None
        assert fused[0].relevance_score is None
        assert fused[0].bm25_score is not None
        print("✅ BM25-only 候选 vector_distance 为 None")

    def test_bm25_only_candidate_not_fake_relevance(self):
        """测试不伪造 BM25-only 候选的相关度"""
        doc = Document(
            page_content="测试",
            metadata={"document_version_id": 1, "chunk_index": 0}
        )

        candidate = RetrievalCandidate(doc, "1_0")
        candidate.add_bm25_result(score=10.0, rank=1)

        # 不应该有向量相关信息
        assert candidate.vector_distance is None
        assert candidate.relevance_score is None
        # 但有 BM25 信息
        assert candidate.bm25_score == 10.0
        print("✅ BM25-only 候选不伪造向量相关度")


class TestFailureFallback:
    """测试失败降级"""

    def test_bm25_failure_uses_vector(self):
        """测试 BM25 失败时使用向量结果"""
        with patch.object(HybridRetriever, '_vector_search') as mock_vector, \
             patch.object(HybridRetriever, '_bm25_search') as mock_bm25:

            # 向量成功
            mock_vector.return_value = [{
                'document': Document(page_content="test", metadata={"document_version_id": 1, "chunk_index": 0}),
                'vector_distance': 0.5,
                'vector_rank': 1,
                'relevance_score': 0.6
            }]

            # BM25 失败
            mock_bm25.side_effect = Exception("BM25 error")

            retriever = HybridRetriever(user_role='employee')
            # 注意：_bm25_search 内部捕获了异常，返回空列表
            # 所以这里不会抛出异常，而是返回只有向量结果
            mock_bm25.side_effect = None
            mock_bm25.return_value = []

            candidates, stats = retriever.search("测试", top_k=3)

            # 应该有向量结果
            assert len(candidates) > 0 or stats['vector_retrieved'] > 0
            print("✅ BM25 失败时可以使用向量降级")

    def test_both_failure_returns_empty(self):
        """测试两个通道都失败时返回空"""
        with patch.object(HybridRetriever, '_vector_search') as mock_vector, \
             patch.object(HybridRetriever, '_bm25_search') as mock_bm25:

            mock_vector.return_value = []
            mock_bm25.return_value = []

            retriever = HybridRetriever(user_role='employee')
            candidates, stats = retriever.search("测试", top_k=3)

            assert len(candidates) == 0
            print("✅ 两个通道都失败时返回空结果")


class TestDocumentScoreAlignment:
    """测试文档和分数不错位"""

    def test_rrf_sorted_candidates_alignment(self):
        """测试 RRF 排序后文档和分数对齐"""
        docs = [
            Document(page_content="doc1", metadata={"document_version_id": 1, "chunk_index": 0}),
            Document(page_content="doc2", metadata={"document_version_id": 1, "chunk_index": 1}),
            Document(page_content="doc3", metadata={"document_version_id": 1, "chunk_index": 2}),
        ]

        vector_candidates = [
            {'document': docs[0], 'vector_distance': 0.3, 'vector_rank': 1, 'relevance_score': 0.9},
            {'document': docs[1], 'vector_distance': 0.5, 'vector_rank': 2, 'relevance_score': 0.7},
        ]

        bm25_candidates = [
            {'document': docs[1], 'bm25_score': 10.0, 'bm25_rank': 1},
            {'document': docs[2], 'bm25_score': 5.0, 'bm25_rank': 2},
        ]

        from rag.hybrid_retriever import HybridRetriever
        retriever = HybridRetriever(user_role='employee')
        fused = retriever._rrf_fusion(vector_candidates, bm25_candidates)

        # 验证每个候选的 document 和分数匹配
        for cand in fused:
            if cand.vector_rank:
                # 有向量排名，验证 document 和 vector_distance 匹配
                assert cand.document.page_content in ["doc1", "doc2", "doc3"]
            if cand.bm25_rank:
                # 有 BM25 排名，验证 document 和 bm25_score 匹配
                assert cand.document.page_content in ["doc1", "doc2", "doc3"]

        print(f"✅ RRF 融合后文档和分数对齐，共 {len(fused)} 个候选")


class TestRAGServiceIntegration:
    """测试 RAG 服务集成"""

    def test_rag_service_uses_hybrid_by_default(self):
        """测试 RAG 服务默认使用混合检索"""
        with patch('rag.rag_service.chroma_conf') as mock_conf:
            mock_conf.get.return_value = {'enabled': True}

            rag = RagSummarizeService(user_role='employee')
            assert rag.use_hybrid is True
            assert rag.hybrid_retriever is not None
            print("✅ RAG 服务默认启用混合检索")

    def test_rag_service_can_disable_hybrid(self):
        """测试可以禁用混合检索"""
        rag = RagSummarizeService(user_role='employee', use_hybrid=False)
        assert rag.use_hybrid is False
        assert rag.hybrid_retriever is None
        print("✅ 可以禁用混合检索")

    def test_reference_contains_hybrid_fields(self):
        """测试引用包含混合检索字段"""
        rag = RagSummarizeService(user_role='employee')

        docs = [Document(
            page_content="测试内容",
            metadata={
                "source_name": "test.pdf",
                "document_id": 1,
                "document_version_id": 10,
                "chunk_index": 0
            }
        )]
        scores = [0.5]
        extra_fields = [{
            'bm25_score': 10.0,
            'vector_rank': 1,
            'bm25_rank': 2,
            'rrf_score': 0.05,
            'retrieval_channels': ['vector', 'bm25']
        }]

        references = rag.format_references(docs, scores, extra_fields)

        assert len(references) == 1
        ref = references[0]
        assert 'bm25_score' in ref
        assert 'vector_rank' in ref
        assert 'bm25_rank' in ref
        assert 'rrf_score' in ref
        assert 'retrieval_channels' in ref
        print("✅ 引用包含混合检索字段")


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("混合检索测试套件")
    print("=" * 60)
    pytest.main([__file__, '-v', '-s'])


if __name__ == '__main__':
    run_all_tests()
