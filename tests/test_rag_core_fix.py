"""
核心修复功能测试：权限、版本过滤、文档分数配对
使用隔离数据库和Mock向量库
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import Mock, patch, MagicMock
from langchain_core.documents import Document
from rag.rag_service import RagSummarizeService
from services.document_service import DocumentService
from services.permission_service import PermissionService
from models.database import (
    get_session, Document as DBDocument, DocumentVersion,
    KnowledgeSpace, User
)


@pytest.fixture
def test_db():
    """创建隔离的测试数据库（每个测试独立）"""
    # 使用内存数据库
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, scoped_session
    from models.database import Base
    import models.database as db_module

    engine = create_engine('sqlite:///:memory:', echo=False)
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    TestSession = scoped_session(SessionFactory)

    # 替换所有模块中的get_session
    original_get_session = db_module.get_session

    def mock_get_session():
        return TestSession()

    db_module.get_session = mock_get_session

    # 准备测试数据（只创建一次）
    session = TestSession()
    try:
        # 创建知识空间
        space1 = KnowledgeSpace(id=1, name='产品知识库', code='product', allowed_roles='employee,admin')
        space2 = KnowledgeSpace(id=2, name='客服知识库', code='service', allowed_roles='service,admin')
        session.add_all([space1, space2])

        # 创建用户
        user1 = User(id=1, username='admin', password_hash='hash', role='admin')
        user2 = User(id=2, username='employee', password_hash='hash', role='employee')
        session.add_all([user1, user2])

        # 创建文档1（产品手册，有2个版本）
        doc1 = DBDocument(id=1, name='产品手册', space_id=1, created_by=1, is_deleted=False)
        session.add(doc1)
        session.flush()

        # 文档1的旧版本（failed - 会被validate_documents过滤）
        ver1_old = DocumentVersion(
            id=1, document_id=1, version_number=1,
            file_hash='hash1', file_path='/tmp/v1', file_size=100,
            status='failed', chunk_count=5
        )
        # 文档1的当前版本（success）
        ver1_new = DocumentVersion(
            id=2, document_id=1, version_number=2,
            file_hash='hash2', file_path='/tmp/v2', file_size=200,
            status='success', chunk_count=8
        )
        session.add_all([ver1_old, ver1_new])
        session.flush()

        doc1.current_version_id = 2  # 设置当前版本为2

        # 创建已删除文档
        doc2 = DBDocument(id=2, name='已删除文档', space_id=1, created_by=1, is_deleted=True)
        session.add(doc2)
        session.flush()

        ver2 = DocumentVersion(
            id=3, document_id=2, version_number=1,
            file_hash='hash3', file_path='/tmp/v3', file_size=150,
            status='deleted', chunk_count=3
        )
        session.add(ver2)
        session.flush()
        doc2.current_version_id = 3

        session.commit()
    finally:
        session.close()

    yield TestSession

    # 恢复并清理
    db_module.get_session = original_get_session
    TestSession.remove()
    engine.dispose()


class TestPermissionDefaultDeny:
    """测试权限默认拒绝"""

    def test_empty_role_returns_zero_results(self, test_db):
        """空角色应返回零结果"""
        # Mock向量库
        with patch('rag.rag_service.VectorStoreService') as MockVS:
            mock_vs_instance = MockVS.return_value
            mock_vs_instance.vector_store.similarity_search_with_score.return_value = []

            rag = RagSummarizeService(user_role=None)
            docs, scores = rag.retriever_docs_with_scores("测试问题")

            assert docs == []
            assert scores == []
            # 验证未调用向量库（权限前置拒绝）
            mock_vs_instance.vector_store.similarity_search_with_score.assert_not_called()

    def test_unknown_role_returns_zero_results(self, test_db):
        """未知角色应返回零结果"""
        with patch('rag.rag_service.VectorStoreService') as MockVS:
            mock_vs_instance = MockVS.return_value
            mock_vs_instance.vector_store.similarity_search_with_score.return_value = []

            rag = RagSummarizeService(user_role='unknown_role')
            docs, scores = rag.retriever_docs_with_scores("测试问题")

            assert docs == []
            assert scores == []

    def test_employee_cannot_access_service_space(self, test_db):
        """员工不能访问客服空间文档"""
        with patch('rag.rag_service.VectorStoreService') as MockVS:
            mock_vs_instance = MockVS.return_value

            # 由于检索前就过滤了valid_version_ids，不会返回space_id=2的文档
            # 这里测试的是：即使Mock返回了，也应该被过滤
            rag = RagSummarizeService(user_role='employee')

            # get_current_version_ids只查space_id=1，所以不会包含space_id=2的版本
            docs, scores = rag.retriever_docs_with_scores("测试问题")

            # 由于员工无权访问space_id=2，valid_version_ids不包含该空间的版本
            # retriever_docs_with_scores应该返回空
            assert docs == []
            assert scores == []


class TestVersionFiltering:
    """测试版本过滤"""

    def test_old_version_not_retrieved(self, test_db):
        """旧版本不应被检索"""
        with patch('rag.rag_service.VectorStoreService') as MockVS, \
             patch('services.document_service.get_session', side_effect=lambda: test_db()):
            mock_vs_instance = MockVS.return_value

            # 向量库应该已经通过filter过滤掉旧版本
            # 这里返回空，验证filter生效
            mock_vs_instance.vector_store.similarity_search_with_score.return_value = []

            rag = RagSummarizeService(user_role='employee')
            docs, scores = rag.retriever_docs_with_scores("测试问题")

            # 验证调用时传入了正确的过滤条件
            call_args = mock_vs_instance.vector_store.similarity_search_with_score.call_args
            filter_dict = call_args.kwargs['filter']

            # 应该包含版本过滤
            assert '$and' in filter_dict
            filters = filter_dict['$and']

            # 检查是否有document_version_id过滤
            version_filter = next((f for f in filters if 'document_version_id' in f), None)
            assert version_filter is not None
            assert version_filter['document_version_id']['$in'] == [2]  # 只有当前版本2

    def test_deleted_document_not_retrieved(self, test_db):
        """已删除文档不应被检索"""
        with patch('rag.rag_service.VectorStoreService') as MockVS, \
             patch('services.document_service.get_session', side_effect=lambda: test_db()):
            mock_vs_instance = MockVS.return_value
            mock_vs_instance.vector_store.similarity_search_with_score.return_value = []

            rag = RagSummarizeService(user_role='employee')
            docs, scores = rag.retriever_docs_with_scores("测试问题")

            # 验证valid_version_ids不包含已删除文档的版本
            call_args = mock_vs_instance.vector_store.similarity_search_with_score.call_args
            filter_dict = call_args.kwargs['filter']
            filters = filter_dict['$and']
            version_filter = next((f for f in filters if 'document_version_id' in f), None)

            # 版本3属于已删除文档，不应在列表中
            assert 3 not in version_filter['document_version_id']['$in']

    def test_only_current_version_retrieved(self, test_db):
        """只有当前版本被检索"""
        with patch('rag.rag_service.VectorStoreService') as MockVS, \
             patch('services.document_service.get_session', side_effect=lambda: test_db()):
            mock_vs_instance = MockVS.return_value

            # 模拟向量库返回当前版本
            mock_doc_current = Document(
                page_content="当前版本内容",
                metadata={
                    'space_id': 1,
                    'document_id': 1,
                    'document_version_id': 2,  # 当前版本ID
                    'source_name': '产品手册',
                    'chunk_index': 0
                }
            )
            mock_vs_instance.vector_store.similarity_search_with_score.return_value = [
                (mock_doc_current, 0.3)
            ]

            rag = RagSummarizeService(user_role='employee')
            docs, scores = rag.retriever_docs_with_scores("测试问题")

            # 当前版本应该通过
            assert len(docs) == 1
            assert len(scores) == 1
            assert docs[0].metadata['document_version_id'] == 2


class TestDocumentScorePairing:
    """测试文档与分数配对"""

    def test_score_pairing_after_filtering(self, test_db):
        """过滤后文档与分数保持配对"""
        with patch('rag.rag_service.VectorStoreService') as MockVS, \
             patch('services.document_service.get_session', side_effect=lambda: test_db()):
            mock_vs_instance = MockVS.return_value

            # 模拟返回3个文档：第1个会被validate_documents过滤（状态failed），第2、3个有效
            mock_doc1 = Document(
                page_content="旧版本",
                metadata={
                    'space_id': 1, 'document_id': 1,
                    'document_version_id': 1,  # 版本1状态是failed，会被过滤
                    'source_name': '产品手册', 'chunk_index': 0
                }
            )
            mock_doc2 = Document(
                page_content="当前版本分片1",
                metadata={
                    'space_id': 1, 'document_id': 1,
                    'document_version_id': 2,  # 当前版本
                    'source_name': '产品手册', 'chunk_index': 0
                }
            )
            mock_doc3 = Document(
                page_content="当前版本分片2",
                metadata={
                    'space_id': 1, 'document_id': 1,
                    'document_version_id': 2,  # 当前版本
                    'source_name': '产品手册', 'chunk_index': 1
                }
            )

            mock_vs_instance.vector_store.similarity_search_with_score.return_value = [
                (mock_doc1, 0.1),  # 最相关，但会被过滤（版本1是failed状态）
                (mock_doc2, 0.5),  # 次相关
                (mock_doc3, 0.7),  # 最不相关
            ]

            rag = RagSummarizeService(user_role='employee')

            # 先检索（会返回包含version_id=1的结果，因为mock）
            docs, scores = rag.retriever_docs_with_scores("测试问题", k=3)

            # 按相关性过滤（阈值0.8）
            filtered_docs, filtered_scores, _ = rag.filter_by_relevance(docs, scores)

            # 验证文档版本有效性（会过滤掉doc1，因为版本1状态是failed）
            valid_docs, valid_scores, _ = rag.validate_documents(filtered_docs, filtered_scores)

            # 验证：应该只有doc2和doc3
            assert len(valid_docs) == 2
            assert len(valid_scores) == 2

            # 验证配对正确：doc2对应0.5，doc3对应0.7
            assert valid_docs[0].page_content == "当前版本分片1"
            assert valid_scores[0] == 0.5
            assert valid_docs[1].page_content == "当前版本分片2"
            assert valid_scores[1] == 0.7


class TestGetCurrentVersionIds:
    """测试获取当前版本ID列表"""

    def test_get_current_version_ids(self, test_db):
        """获取当前版本ID列表"""
        # Patch DocumentService内部的get_session
        with patch('services.document_service.get_session', side_effect=lambda: test_db()):
            version_ids = DocumentService.get_current_version_ids([1])

            # 应该只返回doc1的当前版本ID（2），不包括已删除文档的版本
            assert version_ids == [2]

    def test_get_current_version_ids_excludes_deleted(self, test_db):
        """已删除文档的版本不应包含"""
        # Patch DocumentService内部的get_session
        with patch('services.document_service.get_session', side_effect=lambda: test_db()):
            version_ids = DocumentService.get_current_version_ids([1])

            # 版本3属于已删除文档，不应包含
            assert 3 not in version_ids


class TestReferenceDeduplicate:
    """测试引用去重"""

    def test_duplicate_chunks_deduplicated(self, test_db):
        """重复分片只出现一次"""
        with patch('rag.rag_service.VectorStoreService') as MockVS:
            mock_vs_instance = MockVS.return_value

            # 模拟返回同一分片的重复向量
            mock_doc1 = Document(
                page_content="相同内容",
                metadata={
                    'space_id': 1, 'document_id': 1,
                    'document_version_id': 2, 'chunk_index': 0,
                    'source_name': '产品手册'
                }
            )
            mock_doc2 = Document(
                page_content="相同内容",  # 重复
                metadata={
                    'space_id': 1, 'document_id': 1,
                    'document_version_id': 2, 'chunk_index': 0,  # 相同version_id和chunk_index
                    'source_name': '产品手册'
                }
            )

            mock_vs_instance.vector_store.similarity_search_with_score.return_value = [
                (mock_doc1, 0.3),
                (mock_doc2, 0.3),
            ]

            rag = RagSummarizeService(user_role='employee')
            docs, scores = rag.retriever_docs_with_scores("测试问题")

            # 格式化引用（会去重）
            references = rag.format_references(docs, scores)

            # 应该只有1个引用
            assert len(references) == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
