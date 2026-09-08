
"""
RAG服务类：支持权限感知的检索和带引用的问答
实现可信RAG：基于相关性判断，证据不足时拒答
"""
import time
from typing import List, Dict, Tuple
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from rag.vector_store import VectorStoreService
from utils.prompt_loader import load_rag_prompts
from langchain_core.prompts import PromptTemplate
from model.factory import chat_model
from utils.logger_handler import logger
from services.document_service import DocumentService


class RagSummarizeService(object):
    """RAG问答服务"""

    # 相关性阈值配置（集中管理）
    # Chroma使用L2距离，距离越小越相似
    # 对于归一化的embedding，典型范围是0-2
    # 0.5以下：高度相关
    # 0.5-0.8：中等相关
    # 0.8以上：弱相关
    RELEVANCE_THRESHOLD = 0.8  # 距离阈值

    # 统一拒答内容
    REFUSAL_MESSAGE = "当前知识库中没有找到足够依据，无法确认该问题。建议补充相关文档，或联系人工人员处理。"

    def __init__(self, user_role: str = None):
        """
        初始化RAG服务
        :param user_role: 用户角色，用于权限过滤
        """
        self.user_role = user_role
        self.vector_store = VectorStoreService()
        self.prompt_text = load_rag_prompts()
        self.prompt_template = PromptTemplate.from_template(self.prompt_text)
        self.model = chat_model
        self.chain = self._init_chain()

    def _init_chain(self):
        chain = self.prompt_template | self.model | StrOutputParser()
        return chain

    def retriever_docs_with_scores(self, query: str, k: int = None) -> Tuple[List[Document], List[float]]:
        """
        检索相关文档并返回分数
        :param query: 查询文本
        :param k: 返回文档数量
        :return: (文档列表, 距离分数列表)
        """
        from utils.config_handler import chroma_conf

        if k is None:
            k = chroma_conf.get("k", 3)

        # 获取用户可访问的知识空间
        from services.permission_service import PermissionService
        accessible_space_ids = PermissionService.get_accessible_space_ids(self.user_role) if self.user_role else []

        # 构建过滤条件
        if accessible_space_ids:
            filter_dict = {
                "$and": [
                    {"space_id": {"$in": accessible_space_ids}},
                    {"is_active": {"$eq": True}}
                ]
            }
        else:
            filter_dict = {"is_active": True}

        # 使用 similarity_search_with_score 获取分数
        results = self.vector_store.vector_store.similarity_search_with_score(
            query=query,
            k=k,
            filter=filter_dict
        )

        if not results:
            return [], []

        # 分离文档和分数
        docs = [doc for doc, score in results]
        scores = [score for doc, score in results]

        return docs, scores

    def normalize_score(self, distance: float) -> float:
        """
        将距离转换为相似度分数 (0-1，越大越相似)
        :param distance: L2距离
        :return: 相似度分数
        """
        # L2距离转相似度：使用负指数函数
        # distance=0 -> score=1.0
        # distance=1 -> score≈0.37
        # distance=2 -> score≈0.14
        import math
        return math.exp(-distance)

    def filter_by_relevance(self, docs: List[Document], scores: List[float]) -> Tuple[List[Document], List[float], List[Dict]]:
        """
        按相关性阈值过滤文档
        :param docs: 文档列表
        :param scores: 距离分数列表
        :return: (过滤后文档, 过滤后分数, 详细信息列表)
        """
        filtered_docs = []
        filtered_scores = []
        details = []

        for doc, distance in zip(docs, scores):
            relevance_score = self.normalize_score(distance)

            detail = {
                "raw_distance": distance,
                "relevance_score": relevance_score,
                "source_name": doc.metadata.get("source_name", "未知"),
                "passed": distance <= self.RELEVANCE_THRESHOLD
            }
            details.append(detail)

            if distance <= self.RELEVANCE_THRESHOLD:
                filtered_docs.append(doc)
                filtered_scores.append(distance)

        return filtered_docs, filtered_scores, details

    def validate_documents(self, docs: List[Document]) -> Tuple[List[Document], str]:
        """
        验证文档版本有效性
        过滤掉已下架、删除或非当前版本的文档
        :param docs: 文档列表
        :return: (有效文档列表, 拒答原因)
        """
        valid_docs = []

        for doc in docs:
            metadata = doc.metadata
            document_id = metadata.get("document_id")
            version_id = metadata.get("document_version_id")

            if not document_id or not version_id:
                # 旧数据可能没有这些字段，暂时保留
                valid_docs.append(doc)
                continue

            # 检查文档版本状态
            version_info = DocumentService.get_version_by_id(version_id)
            if not version_info:
                logger.warning(f"版本{version_id}不存在，跳过")
                continue

            # 检查版本状态：必须是success且is_current=True
            if version_info.status != 'success' or not version_info.is_current:
                logger.info(f"版本{version_id}状态={version_info.status}, is_current={version_info.is_current}，跳过")
                continue

            # 检查文档状态：不能是deleted
            document_info = DocumentService.get_document_by_id(document_id)
            if not document_info or document_info.is_deleted:
                logger.info(f"文档{document_id}已删除，跳过")
                continue

            valid_docs.append(doc)

        if not valid_docs:
            return [], "invalid_document_version"

        return valid_docs, ""

    def format_references(self, docs: List[Document], scores: List[float]) -> List[Dict]:
        """
        格式化引用信息（去重、完整字段）
        :param docs: 文档列表
        :param scores: 距离分数列表
        :return: 引用信息列表
        """
        references = []
        seen = set()  # 去重：同一版本同一分片只显示一次

        for idx, (doc, distance) in enumerate(zip(docs, scores), 1):
            metadata = doc.metadata

            # 提取字段
            source_name = metadata.get('source_name', '未知来源')
            document_id = metadata.get('document_id')
            version_id = metadata.get('document_version_id')
            page_num = metadata.get('page', metadata.get('page_number'))
            chunk_index = metadata.get('chunk_index', 0)

            # 构建唯一标识（同一版本同一分片去重）
            ref_key = f"{version_id}_{chunk_index}"
            if ref_key in seen:
                continue
            seen.add(ref_key)

            # 获取版本号
            version_number = None
            if version_id:
                version_info = DocumentService.get_version_by_id(version_id)
                if version_info:
                    version_number = version_info.version_number

            # 构建定位信息
            section_info = []
            if page_num:
                section_info.append(f"第{page_num}页")
            else:
                # TXT文件没有页码，使用分片编号
                section_info.append(f"分片{chunk_index + 1}")

            section = "、".join(section_info) if section_info else f"分片{chunk_index + 1}"

            # 计算相似度分数
            relevance_score = self.normalize_score(distance)

            # 构建引用信息
            ref = {
                'reference_id': idx,
                'source_name': source_name,
                'document_id': document_id,
                'document_version_id': version_id,
                'version_number': version_number,
                'page': page_num,
                'page_number': page_num,  # 兼容字段
                'section': section,
                'chunk_index': chunk_index,
                'relevance_score': round(relevance_score, 4),
                'raw_distance': round(distance, 4),
                'content': doc.page_content[:300] + '...' if len(doc.page_content) > 300 else doc.page_content
            }
            references.append(ref)

        return references

    def build_context_with_citations(self, docs: List[Document]) -> str:
        """
        构建带引用编号的上下文
        :param docs: 文档列表
        :return: 上下文文本
        """
        context_parts = []
        for idx, doc in enumerate(docs, 1):
            context_parts.append(f"【参考资料{idx}】: {doc.page_content}")

        return "\n\n".join(context_parts)

    def rag_summarize(self, query: str) -> Dict:
        """
        RAG问答（带引用和相关性判断）
        :param query: 用户问题
        :return: 包含答案、引用、耗时等信息的字典
        """
        start_time = time.time()

        try:
            # 1. 检索文档和分数
            retrieval_start = time.time()
            docs, scores = self.retriever_docs_with_scores(query)
            retrieval_time_ms = int((time.time() - retrieval_start) * 1000)

            # 记录检索结果
            logger.info(f"检索到{len(docs)}个候选文档")

            # 2. 检查是否有检索结果
            if not docs:
                logger.info(f"无检索结果，拒答：{query[:50]}")
                return {
                    "answer": self.REFUSAL_MESSAGE,
                    "has_answer": False,
                    "references": [],
                    "retrieved_count": 0,
                    "filtered_count": 0,
                    "retrieval_time_ms": retrieval_time_ms,
                    "generation_time_ms": 0,
                    "total_time_ms": int((time.time() - start_time) * 1000),
                    "refusal_reason": "no_retrieval_result"
                }

            # 3. 按相关性阈值过滤
            filtered_docs, filtered_scores, filter_details = self.filter_by_relevance(docs, scores)

            logger.info(f"阈值过滤后剩余{len(filtered_docs)}个文档")
            for detail in filter_details:
                logger.debug(f"  - {detail['source_name']}: distance={detail['raw_distance']:.4f}, "
                           f"relevance={detail['relevance_score']:.4f}, passed={detail['passed']}")

            # 4. 检查是否低于阈值
            if not filtered_docs:
                logger.info(f"所有文档低于相关性阈值，拒答：{query[:50]}")
                return {
                    "answer": self.REFUSAL_MESSAGE,
                    "has_answer": False,
                    "references": [],
                    "retrieved_count": len(docs),
                    "filtered_count": 0,
                    "retrieval_time_ms": retrieval_time_ms,
                    "generation_time_ms": 0,
                    "total_time_ms": int((time.time() - start_time) * 1000),
                    "refusal_reason": "below_relevance_threshold"
                }

            # 5. 验证文档版本有效性
            valid_docs, invalid_reason = self.validate_documents(filtered_docs)
            if not valid_docs:
                logger.info(f"所有文档版本无效，拒答：{query[:50]}")
                return {
                    "answer": self.REFUSAL_MESSAGE,
                    "has_answer": False,
                    "references": [],
                    "retrieved_count": len(docs),
                    "filtered_count": len(filtered_docs),
                    "retrieval_time_ms": retrieval_time_ms,
                    "generation_time_ms": 0,
                    "total_time_ms": int((time.time() - start_time) * 1000),
                    "refusal_reason": invalid_reason
                }

            # 更新分数列表（只保留有效文档的分数）
            valid_scores = filtered_scores[:len(valid_docs)]

            # 6. 构建上下文
            context = self.build_context_with_citations(valid_docs)

            # 7. 生成答案
            generation_start = time.time()
            answer = self.chain.invoke({
                "input": query,
                "context": context,
            })
            generation_time_ms = int((time.time() - generation_start) * 1000)

            # 8. 格式化引用
            references = self.format_references(valid_docs, valid_scores)

            total_time_ms = int((time.time() - start_time) * 1000)

            logger.info(f"RAG问答成功：query='{query[:50]}...', "
                       f"retrieved={len(docs)}, filtered={len(filtered_docs)}, valid={len(valid_docs)}, "
                       f"retrieval={retrieval_time_ms}ms, generation={generation_time_ms}ms, total={total_time_ms}ms")

            return {
                "answer": answer,
                "has_answer": True,
                "references": references,
                "retrieved_count": len(docs),
                "filtered_count": len(filtered_docs),
                "valid_count": len(valid_docs),
                "retrieval_time_ms": retrieval_time_ms,
                "generation_time_ms": generation_time_ms,
                "total_time_ms": total_time_ms,
                "refusal_reason": None,
                "model_name": getattr(self.model, "model_name", "unknown")
            }

        except Exception as e:
            logger.error(f"RAG问答失败：{str(e)}", exc_info=True)
            total_time_ms = int((time.time() - start_time) * 1000)

            # 不向用户暴露堆栈信息
            return {
                "answer": self.REFUSAL_MESSAGE,
                "has_answer": False,
                "references": [],
                "retrieved_count": 0,
                "filtered_count": 0,
                "valid_count": 0,
                "retrieval_time_ms": 0,
                "generation_time_ms": 0,
                "total_time_ms": total_time_ms,
                "refusal_reason": "generation_error",
                "error_internal": str(e)  # 仅用于内部日志
            }

    def rag_summarize_simple(self, query: str) -> str:
        """
        简单RAG问答（仅返回答案文本，向后兼容）
        :param query: 用户问题
        :return: 答案文本
        """
        result = self.rag_summarize(query)
        return result["answer"]


if __name__ == '__main__':
    rag = RagSummarizeService(user_role='employee')
    result = rag.rag_summarize("小户型适合哪些扫地机器人")
    print("答案：", result['answer'])
    print("引用：", result['references'])
    print(f"耗时：检索{result['retrieval_time_ms']}ms, 生成{result['generation_time_ms']}ms, 总计{result['total_time_ms']}ms")
