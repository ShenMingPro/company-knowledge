"""
文档服务单元测试
"""
import os
import sys
import io
import tempfile
import shutil

# 设置stdout编码为UTF-8

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import init_database, get_session, Document, DocumentVersion, User, KnowledgeSpace
from services.document_service import DocumentService
from services.auth_service import AuthService


def setup_test_env():
    """设置测试环境"""
    print("设置测试环境...")
    # 初始化数据库（如果不存在）
    init_database()

    # 确保知识空间存在
    session = get_session()
    try:
        # 检查知识空间
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
            print("创建知识空间: 产品知识库")

        space2 = session.query(KnowledgeSpace).filter_by(id=2).first()
        if not space2:
            space2 = KnowledgeSpace(
                name="企业制度库",
                code="policy",
                description="企业制度",
                allowed_roles="employee,service,admin",
                is_enabled=True
            )
            session.add(space2)
            print("创建知识空间: 企业制度库")

        session.commit()

        # 创建测试用户
        test_user = session.query(User).filter_by(username='test_admin').first()
        if not test_user:
            from services.auth_service import AuthService
            AuthService.create_user('test_admin', 'test123', 'admin')
            print("创建测试用户: test_admin")

        # 获取测试用户ID
        test_user = session.query(User).filter_by(username='test_admin').first()
        return test_user.id
    finally:
        session.close()


def test_sanitize_filename():
    """测试文件名清理"""
    print("\n测试1: 文件名清理")

    test_cases = [
        ("normal.txt", "normal.txt"),
        ("../../../etc/passwd", "passwd"),
        ("file<name>.txt", "file_name_.txt"),
        ("test:file|name.pdf", "test_file_name.pdf"),
    ]

    for input_name, expected in test_cases:
        result = DocumentService.sanitize_filename(input_name)
        assert result == expected, f"期望 {expected}, 得到 {result}"
        print(f"  ✓ {input_name} -> {result}")

    print("✓ 文件名清理测试通过")


def test_create_document_and_version():
    """测试创建文档和版本"""
    print("\n测试2: 创建文档和版本")

    user_id = setup_test_env()

    # 创建临时测试文件
    temp_dir = tempfile.mkdtemp()
    test_file_v1 = os.path.join(temp_dir, "test_doc_v1.txt")
    test_file_v2 = os.path.join(temp_dir, "test_doc_v2.txt")
    test_file_v2_same = os.path.join(temp_dir, "test_doc_v2_same.txt")

    with open(test_file_v1, 'w', encoding='utf-8') as f:
        f.write("This is version 1")

    with open(test_file_v2, 'w', encoding='utf-8') as f:
        f.write("This is version 2 - content changed")

    with open(test_file_v2_same, 'w', encoding='utf-8') as f:
        f.write("This is version 2 - content changed")  # 相同内容

    try:
        # 测试创建第一个版本
        result1 = DocumentService.create_document(
            name="测试文档.txt",
            space_id=1,  # 产品知识库
            user_id=user_id,
            temp_file_path=test_file_v1,
            file_size=os.path.getsize(test_file_v1)
        )

        assert result1['success'], f"创建文档失败: {result1.get('message')}"
        doc_id = result1['document_id']
        version1_id = result1['version_id']
        print(f"  ✓ 创建文档成功，ID: {doc_id}, 版本1 ID: {version1_id}")

        # 验证文件路径不会覆盖
        session = get_session()
        try:
            v1 = session.query(DocumentVersion).filter_by(id=version1_id).first()
            assert v1 is not None
            assert os.path.exists(v1.file_path), "版本1文件不存在"
            print(f"  ✓ 版本1文件路径: {v1.file_path}")
        finally:
            session.close()

        # 测试创建第二个版本（内容改变）
        result2 = DocumentService.create_document(
            name="测试文档.txt",
            space_id=1,
            user_id=user_id,
            temp_file_path=test_file_v2,
            file_size=os.path.getsize(test_file_v2)
        )

        assert result2['success'], f"创建版本2失败: {result2.get('message')}"
        assert result2.get('is_new_version'), "应该创建新版本"
        version2_id = result2['version_id']
        print(f"  ✓ 创建版本2成功，ID: {version2_id}")

        # 验证两个版本文件都存在
        session = get_session()
        try:
            v1 = session.query(DocumentVersion).filter_by(id=version1_id).first()
            v2 = session.query(DocumentVersion).filter_by(id=version2_id).first()

            assert os.path.exists(v1.file_path), "版本1文件应该存在"
            assert os.path.exists(v2.file_path), "版本2文件应该存在"
            assert v1.file_path != v2.file_path, "两个版本文件路径应该不同"
            assert v2.version_number == 2, "版本号应该是2"

            print(f"  ✓ 版本1文件: {v1.file_path}")
            print(f"  ✓ 版本2文件: {v2.file_path}")
            print(f"  ✓ 两个版本文件都独立存在")
        finally:
            session.close()

        # 测试上传相同内容（不应创建新版本）
        result3 = DocumentService.create_document(
            name="测试文档.txt",
            space_id=1,
            user_id=user_id,
            temp_file_path=test_file_v2_same,
            file_size=os.path.getsize(test_file_v2_same)
        )

        assert not result3['success'], "相同内容不应创建新版本"
        assert result3.get('status') == 'unchanged', "状态应该是unchanged"
        print(f"  ✓ 相同内容正确拒绝: {result3['message']}")

        print("✓ 文档和版本创建测试通过")

    finally:
        # 清理临时文件
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_document_operations():
    """测试文档操作（下架、恢复、删除）"""
    print("\n测试3: 文档操作")

    user_id = setup_test_env()

    # 创建测试文档
    temp_dir = tempfile.mkdtemp()
    test_file = os.path.join(temp_dir, "test_ops.txt")

    with open(test_file, 'w', encoding='utf-8') as f:
        f.write("Test document for operations")

    try:
        result = DocumentService.create_document(
            name="操作测试文档.txt",
            space_id=1,
            user_id=user_id,
            temp_file_path=test_file,
            file_size=os.path.getsize(test_file)
        )

        assert result['success']
        doc_id = result['document_id']
        version_id = result['version_id']

        # 模拟索引成功
        DocumentService.update_version_status(version_id, 'success', chunk_count=5)
        print(f"  ✓ 创建文档并模拟索引成功")

        # 测试下架
        deactivate_result = DocumentService.deactivate_document(doc_id)
        assert deactivate_result['success'], f"下架失败: {deactivate_result['message']}"

        session = get_session()
        try:
            version = session.query(DocumentVersion).filter_by(id=version_id).first()
            assert version.status == 'inactive', "版本状态应该是inactive"
            print(f"  ✓ 下架成功，状态: {version.status}")
        finally:
            session.close()

        # 测试恢复
        restore_result = DocumentService.restore_document(doc_id)
        assert restore_result['success'], f"恢复失败: {restore_result['message']}"

        session = get_session()
        try:
            version = session.query(DocumentVersion).filter_by(id=version_id).first()
            assert version.status == 'success', "版本状态应该是success"
            print(f"  ✓ 恢复成功，状态: {version.status}")
        finally:
            session.close()

        # 测试软删除
        delete_result = DocumentService.soft_delete_document(doc_id)
        assert delete_result['success'], f"删除失败: {delete_result['message']}"

        session = get_session()
        try:
            doc = session.query(Document).filter_by(id=doc_id).first()
            assert doc.is_deleted, "文档应该标记为已删除"
            version = session.query(DocumentVersion).filter_by(id=version_id).first()
            assert version.status == 'deleted', "版本状态应该是deleted"
            print(f"  ✓ 软删除成功，is_deleted: {doc.is_deleted}")
        finally:
            session.close()

        print("✓ 文档操作测试通过")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_different_spaces_same_name():
    """测试不同知识空间的同名文档"""
    print("\n测试4: 不同知识空间的同名文档")

    user_id = setup_test_env()

    temp_dir = tempfile.mkdtemp()
    test_file1 = os.path.join(temp_dir, "same_name_1.txt")
    test_file2 = os.path.join(temp_dir, "same_name_2.txt")

    with open(test_file1, 'w', encoding='utf-8') as f:
        f.write("Content in space 1")

    with open(test_file2, 'w', encoding='utf-8') as f:
        f.write("Content in space 2")

    try:
        # 在空间1创建
        result1 = DocumentService.create_document(
            name="相同名称.txt",
            space_id=1,
            user_id=user_id,
            temp_file_path=test_file1,
            file_size=os.path.getsize(test_file1)
        )

        # 在空间2创建
        result2 = DocumentService.create_document(
            name="相同名称.txt",
            space_id=2,
            user_id=user_id,
            temp_file_path=test_file2,
            file_size=os.path.getsize(test_file2)
        )

        assert result1['success'] and result2['success']
        assert result1['document_id'] != result2['document_id'], "应该是两个不同的文档"

        print(f"  ✓ 空间1文档ID: {result1['document_id']}")
        print(f"  ✓ 空间2文档ID: {result2['document_id']}")
        print("✓ 不同空间同名文档测试通过")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("开始文档服务单元测试")
    print("=" * 60)

    try:
        test_sanitize_filename()
        test_create_document_and_version()
        test_document_operations()
        test_different_spaces_same_name()

        print("\n" + "=" * 60)
        print("✓ 所有测试通过！")
        print("=" * 60)
        return True

    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        return False
    except Exception as e:
        print(f"\n✗ 测试出错: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
