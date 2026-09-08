"""
文档服务
"""
import os
import hashlib
import shutil
from datetime import datetime
from models.database import (
    get_session, Document, DocumentVersion, KnowledgeSpace, User
)
from utils.logger_handler import logger


class DocumentService:
    """文档服务"""

    @staticmethod
    def calculate_file_hash(file_path: str) -> str:
        """计算文件SHA-256哈希值"""
        sha256 = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            logger.error(f"计算文件哈希失败：{str(e)}")
            return None

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """清理文件名，防止路径注入"""
        # 只保留文件名，去除路径
        filename = os.path.basename(filename)
        # 移除特殊字符
        invalid_chars = '<>:"|?*\\'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename

    @staticmethod
    def get_version_file_path(document_id: int, version_number: int, filename: str) -> str:
        """
        生成版本文件路径
        :param document_id: 文档ID
        :param version_number: 版本号
        :param filename: 文件名
        :return: 完整路径
        """
        base_dir = f"data/documents/{document_id}"
        os.makedirs(base_dir, exist_ok=True)
        safe_name = DocumentService.sanitize_filename(filename)
        return os.path.join(base_dir, f"v{version_number}_{safe_name}")

    @staticmethod
    def create_document(name: str, space_id: int, user_id: int,
                       temp_file_path: str, file_size: int) -> dict:
        """
        创建新文档
        :param name: 文档名称
        :param space_id: 知识空间ID
        :param user_id: 创建用户ID
        :param temp_file_path: 临时文件路径
        :param file_size: 文件大小
        :return: 创建结果
        """
        session = get_session()
        try:
            # 计算文件哈希
            file_hash = DocumentService.calculate_file_hash(temp_file_path)
            if not file_hash:
                return {"success": False, "message": "文件哈希计算失败"}

            # 检查知识空间是否存在
            space = session.query(KnowledgeSpace).filter_by(id=space_id).first()
            if not space:
                return {"success": False, "message": "知识空间不存在"}

            # 检查是否已存在相同名称的文档（在同一知识空间）
            existing = session.query(Document).filter_by(
                name=name,
                space_id=space_id,
                is_deleted=False
            ).first()

            if existing:
                # 更新现有文档的版本
                return DocumentService._create_new_version(
                    session, existing, temp_file_path, file_size, file_hash, name
                )

            # 创建新文档
            document = Document(
                name=name,
                space_id=space_id,
                created_by=user_id
            )
            session.add(document)
            session.flush()  # 获取document.id

            # 移动文件到正式路径
            final_path = DocumentService.get_version_file_path(document.id, 1, name)
            shutil.copy(temp_file_path, final_path)

            # 创建第一个版本
            version = DocumentVersion(
                document_id=document.id,
                version_number=1,
                file_hash=file_hash,
                file_path=final_path,
                file_size=file_size,
                status='pending'
            )
            session.add(version)
            session.flush()

            # 设置当前版本
            document.current_version_id = version.id
            session.commit()

            logger.info(f"创建文档成功：{name}，版本1")
            return {
                "success": True,
                "message": "文档创建成功",
                "document_id": document.id,
                "version_id": version.id,
                "file_path": final_path
            }

        except Exception as e:
            session.rollback()
            logger.error(f"创建文档失败：{str(e)}", exc_info=True)
            return {"success": False, "message": f"创建失败：{str(e)}"}
        finally:
            session.close()

    @staticmethod
    def _create_new_version(session, document: Document, temp_file_path: str,
                           file_size: int, file_hash: str, name: str) -> dict:
        """为现有文档创建新版本"""
        try:
            # 获取最新版本号
            latest_version = session.query(DocumentVersion).filter_by(
                document_id=document.id
            ).order_by(DocumentVersion.version_number.desc()).first()

            new_version_number = (latest_version.version_number + 1) if latest_version else 1

            # 检查文件是否有变化
            if latest_version and latest_version.file_hash == file_hash:
                return {
                    "success": False,
                    "message": "文件内容未变化，无需创建新版本",
                    "status": "unchanged"
                }

            # 移动文件到正式路径
            final_path = DocumentService.get_version_file_path(
                document.id, new_version_number, name
            )
            shutil.copy(temp_file_path, final_path)

            # 创建新版本
            version = DocumentVersion(
                document_id=document.id,
                version_number=new_version_number,
                file_hash=file_hash,
                file_path=final_path,
                file_size=file_size,
                status='pending'
            )
            session.add(version)
            session.flush()

            # 暂不更新current_version_id，等索引成功后再更新
            session.commit()

            logger.info(f"创建文档新版本：{document.name}，版本{new_version_number}")
            return {
                "success": True,
                "message": f"创建新版本成功（版本{new_version_number}）",
                "document_id": document.id,
                "version_id": version.id,
                "is_new_version": True,
                "file_path": final_path
            }

        except Exception as e:
            session.rollback()
            logger.error(f"创建新版本失败：{str(e)}", exc_info=True)
            raise

    @staticmethod
    def update_version_status(version_id: int, status: str,
                            chunk_count: int = 0, error_message: str = None):
        """
        更新版本状态
        :param version_id: 版本ID
        :param status: 状态
        :param chunk_count: 分片数量
        :param error_message: 错误信息
        """
        session = get_session()
        try:
            version = session.query(DocumentVersion).filter_by(id=version_id).first()
            if not version:
                logger.error(f"版本{version_id}不存在")
                return

            version.status = status
            if chunk_count > 0:
                version.chunk_count = chunk_count
            if error_message:
                version.error_message = error_message
            if status == 'success':
                version.indexed_at = datetime.now()
                # 索引成功后，更新为当前版本并将旧版本设为inactive
                DocumentService._activate_version(session, version)

            session.commit()
            logger.info(f"版本{version_id}状态更新为：{status}")

        except Exception as e:
            session.rollback()
            logger.error(f"更新版本状态失败：{str(e)}", exc_info=True)
        finally:
            session.close()

    @staticmethod
    def _activate_version(session, new_version: DocumentVersion):
        """激活新版本，将旧版本设为inactive"""
        document = new_version.document

        # 将旧版本设为inactive
        old_versions = session.query(DocumentVersion).filter(
            DocumentVersion.document_id == document.id,
            DocumentVersion.id != new_version.id,
            DocumentVersion.status == 'success'
        ).all()

        for old_ver in old_versions:
            old_ver.status = 'inactive'

        # 设置为当前版本
        document.current_version_id = new_version.id

    @staticmethod
    def get_documents(space_id: int = None, include_deleted: bool = False,
                     search_name: str = None, status_filter: str = None) -> list:
        """
        获取文档列表
        :param space_id: 知识空间ID（可选）
        :param include_deleted: 是否包含已删除文档
        :param search_name: 搜索文档名（可选）
        :param status_filter: 状态过滤（可选）
        :return: 文档列表
        """
        session = get_session()
        try:
            query = session.query(Document)

            if space_id:
                query = query.filter_by(space_id=space_id)

            if not include_deleted:
                query = query.filter_by(is_deleted=False)

            if search_name:
                query = query.filter(Document.name.like(f'%{search_name}%'))

            documents = query.order_by(Document.created_at.desc()).all()
            result = []

            for doc in documents:
                current_version = session.query(DocumentVersion).filter_by(
                    id=doc.current_version_id
                ).first()

                # 状态过滤
                if status_filter and current_version:
                    if current_version.status != status_filter:
                        continue

                space = session.query(KnowledgeSpace).filter_by(id=doc.space_id).first()
                creator = session.query(User).filter_by(id=doc.created_by).first()

                result.append({
                    'id': doc.id,
                    'name': doc.name,
                    'space_id': doc.space_id,
                    'space_name': space.name if space else '未知',
                    'version_number': current_version.version_number if current_version else 0,
                    'version_status': current_version.status if current_version else 'unknown',
                    'file_size': current_version.file_size if current_version else 0,
                    'chunk_count': current_version.chunk_count if current_version else 0,
                    'created_by': creator.username if creator else '未知',
                    'created_at': doc.created_at,
                    'indexed_at': current_version.indexed_at if current_version else None,
                    'is_deleted': doc.is_deleted,
                    'error_message': current_version.error_message if current_version else None
                })

            return result
        finally:
            session.close()

    @staticmethod
    def get_document_detail(document_id: int) -> dict:
        """
        获取文档详情（包含所有版本）
        :param document_id: 文档ID
        :return: 文档详情
        """
        session = get_session()
        try:
            document = session.query(Document).filter_by(id=document_id).first()
            if not document:
                return {"success": False, "message": "文档不存在"}

            space = session.query(KnowledgeSpace).filter_by(id=document.space_id).first()
            creator = session.query(User).filter_by(id=document.created_by).first()

            # 获取所有版本
            versions = session.query(DocumentVersion).filter_by(
                document_id=document_id
            ).order_by(DocumentVersion.version_number.desc()).all()

            version_list = []
            for v in versions:
                version_list.append({
                    'id': v.id,
                    'version_number': v.version_number,
                    'file_hash': v.file_hash,
                    'file_path': v.file_path,
                    'file_size': v.file_size,
                    'status': v.status,
                    'chunk_count': v.chunk_count,
                    'error_message': v.error_message,
                    'created_at': v.created_at,
                    'indexed_at': v.indexed_at,
                    'is_current': v.id == document.current_version_id
                })

            return {
                'success': True,
                'document': {
                    'id': document.id,
                    'name': document.name,
                    'space_id': document.space_id,
                    'space_name': space.name if space else '未知',
                    'created_by': creator.username if creator else '未知',
                    'created_at': document.created_at,
                    'is_deleted': document.is_deleted,
                    'current_version_id': document.current_version_id
                },
                'versions': version_list
            }
        finally:
            session.close()

    @staticmethod
    def deactivate_document(document_id: int) -> dict:
        """
        下架文档（将当前版本设为inactive）
        :param document_id: 文档ID
        :return: 操作结果
        """
        session = get_session()
        try:
            document = session.query(Document).filter_by(id=document_id).first()
            if not document:
                return {"success": False, "message": "文档不存在"}

            if document.is_deleted:
                return {"success": False, "message": "文档已被删除"}

            current_version = session.query(DocumentVersion).filter_by(
                id=document.current_version_id
            ).first()

            if not current_version:
                return {"success": False, "message": "文档无有效版本"}

            if current_version.status != 'success':
                return {"success": False, "message": "只能下架状态为success的文档"}

            # 更新版本状态
            current_version.status = 'inactive'
            session.commit()

            logger.info(f"文档{document.name}已下架")
            return {
                "success": True,
                "message": "文档已下架",
                "version_id": current_version.id
            }

        except Exception as e:
            session.rollback()
            logger.error(f"下架文档失败：{str(e)}", exc_info=True)
            return {"success": False, "message": f"下架失败：{str(e)}"}
        finally:
            session.close()

    @staticmethod
    def restore_document(document_id: int, version_id: int = None) -> dict:
        """
        恢复文档（激活指定版本或当前版本）
        :param document_id: 文档ID
        :param version_id: 要恢复的版本ID（可选，默认恢复当前版本）
        :return: 操作结果
        """
        session = get_session()
        try:
            document = session.query(Document).filter_by(id=document_id).first()
            if not document:
                return {"success": False, "message": "文档不存在"}

            # 如果文档被软删除，先恢复文档
            if document.is_deleted:
                document.is_deleted = False

            # 确定要恢复的版本
            if version_id:
                target_version = session.query(DocumentVersion).filter_by(
                    id=version_id,
                    document_id=document_id
                ).first()
            else:
                target_version = session.query(DocumentVersion).filter_by(
                    id=document.current_version_id
                ).first()

            if not target_version:
                return {"success": False, "message": "指定版本不存在"}

            if target_version.status not in ['inactive', 'success']:
                return {"success": False, "message": f"版本状态为{target_version.status}，无法恢复"}

            # 将所有其他版本设为inactive
            other_versions = session.query(DocumentVersion).filter(
                DocumentVersion.document_id == document_id,
                DocumentVersion.id != target_version.id
            ).all()
            for v in other_versions:
                if v.status == 'success':
                    v.status = 'inactive'

            # 激活目标版本
            target_version.status = 'success'
            document.current_version_id = target_version.id

            session.commit()

            logger.info(f"文档{document.name}已恢复，当前版本{target_version.version_number}")
            return {
                "success": True,
                "message": f"文档已恢复到版本{target_version.version_number}",
                "version_id": target_version.id
            }

        except Exception as e:
            session.rollback()
            logger.error(f"恢复文档失败：{str(e)}", exc_info=True)
            return {"success": False, "message": f"恢复失败：{str(e)}"}
        finally:
            session.close()

    @staticmethod
    def soft_delete_document(document_id: int) -> dict:
        """
        软删除文档
        :param document_id: 文档ID
        :return: 删除结果
        """
        session = get_session()
        try:
            document = session.query(Document).filter_by(id=document_id).first()
            if not document:
                return {"success": False, "message": "文档不存在"}

            if document.is_deleted:
                return {"success": False, "message": "文档已被删除"}

            # 标记文档为已删除
            document.is_deleted = True

            # 将所有版本标记为deleted或inactive
            versions = session.query(DocumentVersion).filter_by(
                document_id=document_id
            ).all()
            for v in versions:
                if v.status == 'success':
                    v.status = 'deleted'
                elif v.status in ['pending', 'processing']:
                    v.status = 'deleted'

            session.commit()

            logger.info(f"文档{document.name}已软删除")
            return {"success": True, "message": "文档已删除"}

        except Exception as e:
            session.rollback()
            logger.error(f"删除文档失败：{str(e)}", exc_info=True)
            return {"success": False, "message": f"删除失败：{str(e)}"}
        finally:
            session.close()

    @staticmethod
    def reindex_version(version_id: int) -> dict:
        """
        准备重新索引版本（只更新状态为processing，实际索引由调用方执行）
        :param version_id: 版本ID
        :return: 操作结果
        """
        session = get_session()
        try:
            version = session.query(DocumentVersion).filter_by(id=version_id).first()
            if not version:
                return {"success": False, "message": "版本不存在"}

            if version.status not in ['failed', 'success', 'inactive']:
                return {"success": False, "message": f"版本状态为{version.status}，无法重新索引"}

            # 更新状态为processing
            version.status = 'processing'
            version.error_message = None
            session.commit()

            logger.info(f"版本{version_id}开始重新索引")
            return {
                "success": True,
                "message": "准备重新索引",
                "version_id": version_id,
                "document_id": version.document_id,
                "file_path": version.file_path
            }

        except Exception as e:
            session.rollback()
            logger.error(f"准备重新索引失败：{str(e)}", exc_info=True)
            return {"success": False, "message": f"操作失败：{str(e)}"}
        finally:
            session.close()
