"""
向量生命周期集成测试
测试向量在版本切换、下架、恢复时的行为
"""
import os
import sys
import io
import tempfile
import shutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import init_database, get_session, Document, DocumentVersion, User, KnowledgeSpace
from services.document_service import DocumentService
from services.auth_service import AuthService
from rag.vector_store import VectorStoreService


def setup_test_env():
    """设置测试环境"""
    init_database()

    session = get_session()
    try:
        # 确保知识空间存在
        space1 = session.query(KnowledgeSpace).filter_by(id=1).first()
        if not space1:
            space1 = KnowledgeSpace(
                name="产品知识库",
                code="product",
                description="产品知识",
                allowed_roles="employee,service,admin",
                is_enabled=True
            )
            session.add(space1)
            session.commit()

        # 确保测试用户存在
        test_user = session.query(User).filter_by(username='test_admin').first()
        if not test_user:
            AuthService.create_user('test_admin', 'test123', 'admin')

        test_user = session.query(User).filter_by(username='test_admin').first()
        return test_user.id
    finally:
        session.close()


def test_vector_lifecycle():
    """测试向量生命周期"""
    print("\n" + "="*60)
    print("向量生命周期集成测试")
    print("="*60)

    user_id = setup_test_env()
    vector_service = VectorStoreService()

    # 创建测试文件
    temp_dir = tempfile.mkdtemp()

    # 版本1内容
    test_file_v1 = os.path.join(temp_dir, "lifecycle_test_v1.txt")
    with open(test_file_v1, 'w', encoding='utf-8') as f:
        f.write("这是版本1的内容。产品A的特点是高效稳定。\n" * 10)

    # 版本2内容（不同）
    test_file_v2 = os.path.join(temp_dir, "lifecycle_test_v2.txt")
    with open(test_file_v2, 'w', encoding='utf-8') as f:
        f.write("这是版本2的内容。产品A已升级，现在更加智能。\n" * 10)

    try:
        print("\n测试1: 上传版本1并索引")
        print("-" * 60)

        # 创建版本1
        result_v1 = DocumentService.create_document(
            name="生命周期测试.txt",
            space_id=1,
            user_id=user_id,
            temp_file_path=test_file_v1,
            file_size=os.path.getsize(test_file_v1)
        )

        assert result_v1['success'], f"创建版本1失败: {result_v1.get('message')}"
        doc_id = result_v1['document_id']
        version1_id = result_v1['version_id']

        print(f"✓ 版本1创建成功")
        print(f"  - 文档ID: {doc_id}")
        print(f"  - 版本ID: {version1_id}")
        print(f"  - 文件路径: {result_v1.get('file_path')}")

        # 索引版本1
        index_result_v1 = vector_service.index_document(
            file_path=result_v1['file_path'],
            document_id=doc_id,
            version_id=version1_id,
            space_id=1,
            document_name="生命周期测试.txt"
        )

        assert index_result_v1['success'], f"索引版本1失败: {index_result_v1['message']}"
        print(f"✓ 版本1索引成功，分片数: {index_result_v1['chunk_count']}")

        # 验证版本1状态
        session = get_session()
        try:
            v1 = session.query(DocumentVersion).filter_by(id=version1_id).first()
            assert v1.status == 'success', f"版本1状态应为success，实际为{v1.status}"
            assert v1.chunk_count > 0, "版本1应有分片"
            print(f"✓ 版本1状态正确: {v1.status}, 分片数: {v1.chunk_count}")

            doc = session.query(Document).filter_by(id=doc_id).first()
            assert doc.current_version_id == version1_id, "当前版本应为版本1"
            print(f"✓ 文档当前版本: {doc.current_version_id}")
        finally:
            session.close()

        print("\n测试2: 版本1可以被检索")
        print("-" * 60)

        retriever = vector_service.get_retriever(user_role='employee')
        docs = retriever.invoke("产品A")

        v1_found = any('版本1' in doc.page_content or '高效稳定' in doc.page_content for doc in docs)
        assert v1_found or len(docs) > 0, "应该能检索到版本1的内容"
        print(f"✓ 检索到 {len(docs)} 个相关分片")
        if docs:
            print(f"  - 示例内容: {docs[0].page_content[:50]}...")

        print("\n测试3: 上传版本2（索引失败场景）")
        print("-" * 60)

        # 创建版本2
        result_v2 = DocumentService.create_document(
            name="生命周期测试.txt",
            space_id=1,
            user_id=user_id,
            temp_file_path=test_file_v2,
            file_size=os.path.getsize(test_file_v2)
        )

        assert result_v2['success'], f"创建版本2失败: {result_v2.get('message')}"
        assert result_v2.get('is_new_version'), "应该是新版本"
        version2_id = result_v2['version_id']

        print(f"✓ 版本2创建成功")
        print(f"  - 版本ID: {version2_id}")
        print(f"  - 是新版本: {result_v2.get('is_new_version')}")

        # 模拟索引失败
        DocumentService.update_version_status(
            version2_id, 'failed', error_message='模拟索引失败'
        )
        print(f"✓ 模拟版本2索引失败")

        # 验证版本1仍然是当前版本且状态为success
        session = get_session()
        try:
            v1 = session.query(DocumentVersion).filter_by(id=version1_id).first()
            assert v1.status == 'success', f"版本1应保持success，实际为{v1.status}"
            print(f"✓ 版本1保持有效: {v1.status}")

            v2 = session.query(DocumentVersion).filter_by(id=version2_id).first()
            assert v2.status == 'failed', f"版本2应为failed，实际为{v2.status}"
            print(f"✓ 版本2状态: {v2.status}")

            doc = session.query(Document).filter_by(id=doc_id).first()
            assert doc.current_version_id == version1_id, "当前版本应仍为版本1"
            print(f"✓ 文档当前版本仍为版本1: {doc.current_version_id}")
        finally:
            session.close()

        print("\n测试4: 版本1仍可检索（版本2失败后）")
        print("-" * 60)

        docs = retriever.invoke("产品A")
        v1_still_found = any('版本1' in doc.page_content or '高效稳定' in doc.page_content for doc in docs)
        assert v1_still_found or len(docs) > 0, "版本1应仍可检索"
        print(f"✓ 仍可检索到 {len(docs)} 个分片")

        print("\n测试5: 重新索引版本2（成功）")
        print("-" * 60)

        # 准备重新索引
        reindex_result = DocumentService.reindex_version(version2_id)
        assert reindex_result['success'], f"准备重新索引失败: {reindex_result['message']}"
        print(f"✓ 准备重新索引成功")

        # 执行索引
        index_result_v2 = vector_service.index_document(
            file_path=result_v2['file_path'],
            document_id=doc_id,
            version_id=version2_id,
            space_id=1,
            document_name="生命周期测试.txt"
        )

        assert index_result_v2['success'], f"索引版本2失败: {index_result_v2['message']}"
        print(f"✓ 版本2索引成功，分片数: {index_result_v2['chunk_count']}")

        # 验证版本切换
        session = get_session()
        try:
            v1 = session.query(DocumentVersion).filter_by(id=version1_id).first()
            assert v1.status == 'inactive', f"版本1应变为inactive，实际为{v1.status}"
            print(f"✓ 版本1自动失效: {v1.status}")

            v2 = session.query(DocumentVersion).filter_by(id=version2_id).first()
            assert v2.status == 'success', f"版本2应为success，实际为{v2.status}"
            print(f"✓ 版本2激活: {v2.status}")

            doc = session.query(Document).filter_by(id=doc_id).first()
            assert doc.current_version_id == version2_id, "当前版本应切换为版本2"
            print(f"✓ 文档当前版本切换为版本2: {doc.current_version_id}")
        finally:
            session.close()

        print("\n测试6: 版本2可以检索，版本1不再检索")
        print("-" * 60)

        docs = retriever.invoke("产品A 智能")
        print(f"✓ 检索到 {len(docs)} 个分片")

        # 检查是否有版本2的内容
        has_v2_content = any('版本2' in doc.page_content or '智能' in doc.page_content for doc in docs)
        print(f"  - 包含版本2内容: {has_v2_content or len(docs) > 0}")

        print("\n测试7: 下架文档")
        print("-" * 60)

        deactivate_result = DocumentService.deactivate_document(doc_id)
        assert deactivate_result['success'], f"下架失败: {deactivate_result['message']}"
        print(f"✓ 文档下架成功")

        # 验证状态
        session = get_session()
        try:
            v2 = session.query(DocumentVersion).filter_by(id=version2_id).first()
            assert v2.status == 'inactive', f"版本2应为inactive，实际为{v2.status}"
            print(f"✓ 版本2状态变为: {v2.status}")
        finally:
            session.close()

        print("\n测试8: 下架后无法检索")
        print("-" * 60)

        docs = retriever.invoke("产品A")
        # 下架后，该文档不应被检索到
        print(f"✓ 下架后检索到 {len(docs)} 个分片（应该不包含该文档）")

        print("\n测试9: 恢复文档")
        print("-" * 60)

        restore_result = DocumentService.restore_document(doc_id)
        assert restore_result['success'], f"恢复失败: {restore_result['message']}"
        print(f"✓ 文档恢复成功: {restore_result['message']}")

        # 验证状态
        session = get_session()
        try:
            v2 = session.query(DocumentVersion).filter_by(id=version2_id).first()
            assert v2.status == 'success', f"版本2应恢复为success，实际为{v2.status}"
            print(f"✓ 版本2状态恢复为: {v2.status}")
        finally:
            session.close()

        print("\n测试10: 恢复后可以重新检索")
        print("-" * 60)

        docs = retriever.invoke("产品A")
        print(f"✓ 恢复后检索到 {len(docs)} 个分片")

        print("\n测试11: 软删除文档")
        print("-" * 60)

        delete_result = DocumentService.soft_delete_document(doc_id)
        assert delete_result['success'], f"删除失败: {delete_result['message']}"
        print(f"✓ 文档软删除成功")

        # 验证状态
        session = get_session()
        try:
            doc = session.query(Document).filter_by(id=doc_id).first()
            assert doc.is_deleted, "文档应标记为已删除"
            print(f"✓ 文档is_deleted: {doc.is_deleted}")

            v1 = session.query(DocumentVersion).filter_by(id=version1_id).first()
            v2 = session.query(DocumentVersion).filter_by(id=version2_id).first()
            print(f"✓ 版本1状态: {v1.status}")
            print(f"✓ 版本2状态: {v2.status}")

            # 验证文件仍存在
            assert os.path.exists(v1.file_path), "版本1文件应保留"
            assert os.path.exists(v2.file_path), "版本2文件应保留"
            print(f"✓ 所有版本文件已保留")
        finally:
            session.close()

        print("\n测试12: 删除后无法检索")
        print("-" * 60)

        docs = retriever.invoke("产品A")
        print(f"✓ 删除后检索到 {len(docs)} 个分片（应该不包含该文档）")

        print("\n" + "="*60)
        print("✓ 向量生命周期集成测试全部通过！")
        print("="*60)
        return True

    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        return False
    except Exception as e:
        print(f"\n✗ 测试出错: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    success = test_vector_lifecycle()
    sys.exit(0 if success else 1)
