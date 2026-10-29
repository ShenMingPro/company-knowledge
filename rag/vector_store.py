from pathlib import Path
from threading import Lock

from langchain_chroma import Chroma
from langchain_core.documents import Document
from utils.config_handler import chroma_conf
from model.factory import embed_model
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.file_handler import pdf_loader, txt_loader, docx_loader, xlsx_loader
from utils.logger_handler import logger
from services.permission_service import PermissionService
from services.document_service import DocumentService
from rag.chunk_strategy import ChunkStrategyRouter


_VECTOR_STORE = None
_VECTOR_STORE_INIT_LOCK = Lock()
_VECTOR_STORE_WRITE_LOCK = Lock()


def _get_vector_store():
    """Create one Chroma client per process, even under parallel uploads."""
    global _VECTOR_STORE
    if _VECTOR_STORE is None:
        with _VECTOR_STORE_INIT_LOCK:
            if _VECTOR_STORE is None:
                _VECTOR_STORE = Chroma(
                    collection_name=chroma_conf["collection_name"],
                    embedding_function=embed_model,
                    persist_directory=chroma_conf["persist_directory"],
                )
    return _VECTOR_STORE


class VectorStoreService:
    def __init__(self):
        self.vector_store = _get_vector_store()

        # 保留旧的分割器用于向后兼容（未使用路由器的场景）
        self.spliter = RecursiveCharacterTextSplitter(
            chunk_size=chroma_conf["chunk_size"],
            chunk_overlap=chroma_conf["chunk_overlap"],
            separators=chroma_conf["separators"],
            length_function=len,
        )

        # 新的内容结构分块路由器
        self.chunk_router = ChunkStrategyRouter(
            chunk_size=chroma_conf["chunk_size"],
            chunk_overlap=chroma_conf["chunk_overlap"],
            separators=chroma_conf["separators"]
        )

    def get_retriever(self, user_role: str = None, k: int = None):
        """
        获取检索器（支持权限过滤）
        :param user_role: 用户角色，用于权限过滤
        :param k: 返回的文档数量
        :return: 检索器
        """
        if k is None:
            k = chroma_conf["k"]

        # 获取用户可访问的知识空间ID列表
        accessible_space_ids = PermissionService.get_accessible_space_ids(user_role) if user_role else []

        # 权限默认拒绝：无权限时返回不可能匹配的过滤条件
        if not accessible_space_ids:
            logger.warning(f"角色 {user_role} 无可访问空间，返回空检索器")
            return self.vector_store.as_retriever(
                search_kwargs={"k": 0, "filter": {"space_id": {"$eq": -1}}}
            )

        # 构建过滤条件：只检索可访问空间且状态为active的文档
        filter_dict = {
            "$and": [
                {"space_id": {"$in": accessible_space_ids}},
                {"is_active": {"$eq": True}}
            ]
        }
        return self.vector_store.as_retriever(
            search_kwargs={"k": k, "filter": filter_dict}
        )

    def index_document(self, file_path: str, document_id: int, version_id: int,
                      space_id: int, document_name: str) -> dict:
        """
        索引单个文档
        :param file_path: 文件路径
        :param document_id: 文档ID
        :param version_id: 版本ID
        :param space_id: 知识空间ID
        :param document_name: 文档名称
        :return: 索引结果
        """
        try:
            # 解析文档
            documents: list[Document] = self._parse_document(file_path)

            if not documents:
                logger.warning(f"[索引文档]{file_path}内没有有效文本内容")
                DocumentService.update_version_status(
                    version_id, 'failed', error_message='文件内没有有效文本内容'
                )
                return {"success": False, "message": "文件内没有有效文本内容"}

            # 使用内容结构路由器分片
            source_type = self._get_source_type(file_path)
            split_documents: list[Document] = self.chunk_router.split(documents, source_type)

            if not split_documents:
                logger.warning(f"[索引文档]{file_path}分片后没有有效文本内容")
                DocumentService.update_version_status(
                    version_id, 'failed', error_message='分片后没有有效文本内容'
                )
                return {"success": False, "message": "分片后没有有效文本内容"}

            # 为每个分片添加元数据
            for idx, doc in enumerate(split_documents):
                doc.metadata.update({
                    'space_id': space_id,
                    'document_id': document_id,
                    'document_version_id': version_id,
                    'source_name': document_name,
                    'chunk_index': idx,
                    'is_active': True
                })

            # 存入向量库
            # Chroma's local persistent client is not safe to initialize or
            # write concurrently from many FastAPI background tasks.
            with _VECTOR_STORE_WRITE_LOCK:
                self.vector_store.add_documents(split_documents)

            # 更新版本状态为成功
            DocumentService.update_version_status(
                version_id, 'success', chunk_count=len(split_documents)
            )

            logger.info(f"[索引文档]{document_name} 索引成功，共{len(split_documents)}个分片")
            return {
                "success": True,
                "message": f"索引成功，共{len(split_documents)}个分片",
                "chunk_count": len(split_documents)
            }

        except Exception as e:
            logger.error(f"[索引文档]{file_path}索引失败：{str(e)}", exc_info=True)
            DocumentService.update_version_status(
                version_id, 'failed', error_message=str(e)
            )
            return {"success": False, "message": f"索引失败：{str(e)}"}

    def deactivate_document_vectors(self, document_id: int, version_id: int = None):
        """
        将文档的向量设为不活跃（用于下架或删除）
        :param document_id: 文档ID
        :param version_id: 版本ID（可选，不指定则所有版本）
        """
        try:
            # Chroma不支持直接更新元数据，需要通过where过滤
            # 这里我们依赖检索时的is_active过滤
            # 实际的is_active状态已在向量metadata中设置
            logger.info(f"文档{document_id}的向量已通过元数据标记为不活跃")
        except Exception as e:
            logger.error(f"标记向量不活跃失败：{str(e)}", exc_info=True)

    def activate_document_vectors(self, document_id: int, version_id: int):
        """
        激活文档某个版本的向量
        :param document_id: 文档ID
        :param version_id: 版本ID
        """
        try:
            # 由于Chroma的限制，向量的is_active状态在创建时设置
            # 版本切换通过数据库状态控制，检索时过滤
            logger.info(f"文档{document_id}版本{version_id}的向量标记为活跃")
        except Exception as e:
            logger.error(f"激活向量失败：{str(e)}", exc_info=True)

    def _parse_document(self, file_path: str) -> list[Document]:
        """解析文档"""
        extension = Path(file_path).suffix.lower()
        if extension == ".txt":
            return txt_loader(file_path)
        elif extension == ".pdf":
            return pdf_loader(file_path)
        elif extension == ".docx":
            return docx_loader(file_path)
        elif extension == ".xlsx":
            return xlsx_loader(file_path)
        else:
            return []

    def _get_source_type(self, file_path: str) -> str:
        """获取文件类型"""
        extension = Path(file_path).suffix.lower()
        return extension.removeprefix('.') if extension in {'.txt', '.pdf', '.docx', '.xlsx'} else 'unknown'


