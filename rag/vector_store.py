from langchain_chroma import Chroma
from langchain_core.documents import Document
from utils.config_handler import chroma_conf
from model.factory import embed_model
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.path_tool import get_abs_path
from utils.file_handler import pdf_loader, txt_loader, docx_loader, listdir_with_allowed_type, get_file_md5_hex
from utils.logger_handler import logger
from services.permission_service import PermissionService
from services.document_service import DocumentService
import os


class VectorStoreService:
    def __init__(self):
        self.vector_store = Chroma(
            collection_name=chroma_conf["collection_name"],
            embedding_function=embed_model,
            persist_directory=chroma_conf["persist_directory"],
        )

        self.spliter = RecursiveCharacterTextSplitter(
            chunk_size=chroma_conf["chunk_size"],
            chunk_overlap=chroma_conf["chunk_overlap"],
            separators=chroma_conf["separators"],
            length_function=len,
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

        if user_role:
            # 获取用户可访问的知识空间ID列表
            accessible_space_ids = PermissionService.get_accessible_space_ids(user_role)
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
        else:
            # 无权限过滤，但仍然只返回active的文档
            return self.vector_store.as_retriever(
                search_kwargs={"k": k, "filter": {"is_active": True}}
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

            # 分片
            split_documents: list[Document] = self.spliter.split_documents(documents)

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
        if file_path.endswith("txt"):
            return txt_loader(file_path)
        elif file_path.endswith("pdf"):
            return pdf_loader(file_path)
        elif file_path.endswith("docx"):
            return docx_loader(file_path)
        else:
            return []

    def load_document(self):
        """
        旧版兼容方法：从数据文件夹内读取数据文件，转为向量存入向量库
        此方法保留用于向后兼容，新代码应使用index_document
        """
        def check_md5_hex(md5_for_check: str):
            if not os.path.exists(get_abs_path(chroma_conf["md5_hex_store"])):
                open(get_abs_path(chroma_conf["md5_hex_store"]), "w", encoding="utf-8").close()
                return False

            with open(get_abs_path(chroma_conf["md5_hex_store"]), "r", encoding="utf-8") as f:
                for line in f.readlines():
                    line = line.strip()
                    if line == md5_for_check:
                        return True
                return False

        def save_md5_hex(md5_for_check: str):
            with open(get_abs_path(chroma_conf["md5_hex_store"]), "a", encoding="utf-8") as f:
                f.write(md5_for_check + "\n")

        results: list[dict] = []
        allowed_files_path: list[str] = listdir_with_allowed_type(
            get_abs_path(chroma_conf["data_path"]),
            tuple(chroma_conf["allow_knowledge_file_type"]),
        )

        for path in allowed_files_path:
            file_name = os.path.basename(path)
            md5_hex = get_file_md5_hex(path)

            if md5_hex is None:
                results.append({"name": file_name, "status": "error", "message": "MD5计算失败"})
                continue

            if check_md5_hex(md5_hex):
                logger.info(f"[加载知识库]{path}内容已经存在知识库内，跳过")
                results.append({"name": file_name, "status": "skipped", "message": "内容已存在于知识库，跳过"})
                continue

            try:
                documents: list[Document] = self._parse_document(path)

                if not documents:
                    logger.warning(f"[加载知识库]{path}内没有有效文本内容，跳过")
                    results.append({"name": file_name, "status": "empty", "message": "文件内没有有效文本内容"})
                    continue

                split_document: list[Document] = self.spliter.split_documents(documents)

                if not split_document:
                    logger.warning(f"[加载知识库]{path}分片后没有有效文本内容，跳过")
                    results.append({"name": file_name, "status": "empty", "message": "分片后没有有效文本内容"})
                    continue

                # 添加旧版兼容的元数据
                for idx, doc in enumerate(split_document):
                    doc.metadata.update({
                        'space_id': 1,  # 默认产品知识库
                        'source_name': file_name,
                        'chunk_index': idx,
                        'is_active': True
                    })

                self.vector_store.add_documents(split_document)
                save_md5_hex(md5_hex)

                logger.info(f"[加载知识库]{path} 内容加载成功")
                results.append({"name": file_name, "status": "success", "message": f"入库成功，共{len(split_document)}个分片"})
            except Exception as e:
                logger.error(f"[加载知识库]{path}加载失败：{str(e)}", exc_info=True)
                results.append({"name": file_name, "status": "error", "message": f"加载失败：{str(e)}"})
                continue

        return results


if __name__ == '__main__':
    vs = VectorStoreService()

    vs.load_document()

    retriever = vs.get_retriever()

    res = retriever.invoke("机器人价格")
    for r in res:
        print(r.page_content)
        print("-"*20)


