"""
快速初始化知识库：上传测试文档
"""
import sys
import io
import os

# 设置UTF-8输出

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.document_service import DocumentService
from rag.vector_store import VectorStoreService
from models.database import get_session, User, KnowledgeSpace
from utils.logger_handler import logger

def get_or_create_admin():
    """获取或创建admin用户"""
    session = get_session()
    try:
        admin = session.query(User).filter_by(username='admin').first()
        if not admin:
            print("admin用户不存在，请先运行应用创建用户")
            return None
        return admin.id
    finally:
        session.close()

def ensure_knowledge_spaces():
    """确保知识空间存在"""
    session = get_session()
    try:
        spaces = session.query(KnowledgeSpace).all()
        if not spaces:
            print("知识空间不存在，创建默认空间...")
            space1 = KnowledgeSpace(name='产品知识库', description='扫地机器人产品相关知识')
            space2 = KnowledgeSpace(name='售后服务', description='售后服务相关知识')
            session.add(space1)
            session.add(space2)
            session.commit()
            print("✅ 知识空间创建成功")
        return session.query(KnowledgeSpace).all()
    finally:
        session.close()

def upload_documents():
    """上传data目录下的文档"""
    print("=" * 60)
    print("初始化知识库")
    print("=" * 60)

    # 获取admin用户
    admin_id = get_or_create_admin()
    if not admin_id:
        print("❌ 未找到admin用户")
        return

    # 确保知识空间存在
    spaces = ensure_knowledge_spaces()
    product_space = next((s for s in spaces if '产品' in s.name), spaces[0])

    # 扫描data目录
    data_dir = "data"
    files_to_upload = [
        "扫地机器人100问.pdf",
        "扫地机器人100问2.txt",
        "扫拖一体机器人100问.txt",
        "选购指南.txt",
        "维护保养.txt",
        "故障排除.txt"
    ]

    vector_service = VectorStoreService()
    results = []

    for filename in files_to_upload:
        file_path = os.path.join(data_dir, filename)
        if not os.path.exists(file_path):
            print(f"⚠️  文件不存在：{filename}")
            continue

        print(f"\n上传：{filename}")

        # 清理文件名
        safe_name = DocumentService.sanitize_filename(filename)
        file_size = os.path.getsize(file_path)

        # 创建文档记录（直接使用原始文件路径，不走临时目录）
        # 先复制到临时位置
        temp_dir = "data/temp"
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f"temp_{safe_name}")

        import shutil
        shutil.copy2(file_path, temp_path)

        doc_result = DocumentService.create_document(
            name=safe_name,
            space_id=product_space.id,
            user_id=admin_id,
            temp_file_path=temp_path,
            file_size=file_size
        )

        if doc_result["success"]:
            # 索引文档
            index_result = vector_service.index_document(
                file_path=doc_result.get("file_path", temp_path),
                document_id=doc_result["document_id"],
                version_id=doc_result["version_id"],
                space_id=product_space.id,
                document_name=safe_name
            )

            if index_result["success"]:
                print(f"  ✅ {index_result['message']}")
            else:
                print(f"  ❌ 索引失败：{index_result['message']}")

            results.append({
                "name": filename,
                "success": index_result["success"]
            })
        else:
            status = doc_result.get("status")
            if status == "unchanged":
                print(f"  ℹ️  {doc_result['message']}")
            else:
                print(f"  ❌ 创建失败：{doc_result['message']}")

        # 清理临时文件
        try:
            os.remove(temp_path)
        except:
            pass

    # 统计
    print("\n" + "=" * 60)
    print("上传完成")
    print("=" * 60)
    success_count = sum(1 for r in results if r["success"])
    print(f"成功：{success_count}/{len(results)}")

    # 验证
    session = get_session()
    try:
        from models.database import Document
        doc_count = session.query(Document).filter_by(is_deleted=False).count()
        print(f"数据库文档总数：{doc_count}")
    finally:
        session.close()

if __name__ == '__main__':
    upload_documents()
