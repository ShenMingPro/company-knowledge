
"""
RAG服务类：支持权限感知的检索和带引用的问答
实现可信RAG：基于相关性判断，证据不足时拒答
支持混合检索：向量检索 + BM25 关键词检索 + RRF 融合
"""
import time
from typing import List, Dict, Tuple, Optional, Any, Callable
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from rag.vector_store import VectorStoreService
from rag.hybrid_retriever import HybridRetriever
from rag.query_rewriter import create_query_rewriter, RewriteResult
from utils.prompt_loader import load_rag_prompts
from langchain_core.prompts import PromptTemplate
from model.factory import chat_model
from utils.logger_handler import logger
from services.document_service import DocumentService
from utils.config_handler import chroma_conf


class RagSummarizeService(object):
    """RAG问答服务"""

    # 相关性阈值配置（集中管理）
    # Chroma使用L2距离，距离越小越相似
    # 对于归一化的embedding，典型范围是0-2
    # 根据实际测试调整：
    # 0-0.5：高度相关
    # 0.5-0.9：中等相关，通常包含答案
    # 0.9-1.3：弱相关，可能包含部分信息
    # 1.3以上：不相关
    RELEVANCE_THRESHOLD = 0.9  # 距离阈值（收紧以减少无关文档，防止幻觉）

    # 统一拒答内容
    REFUSAL_MESSAGE = "当前知识库中没有找到足够依据，无法确认该问题。建议补充相关文档，或联系人工人员处理。"

    def __init__(self, user_role: str = None, use_hybrid: bool = None, use_query_rewrite: bool = None, use_reranker: bool = None):
        """
        初始化RAG服务
        :param user_role: 用户角色，用于权限过滤
        :param use_hybrid: 是否使用混合检索（None=使用配置，True=强制混合，False=纯向量）
        :param use_query_rewrite: 是否使用 Query 改写（None=使用配置，True=启用，False=禁用）
        :param use_reranker: 是否使用 Reranker 精排（None=使用配置，True=启用，False=禁用）
        """
        self.user_role = user_role
        self.vector_store = VectorStoreService()
        self.prompt_text = load_rag_prompts()
        self.prompt_template = PromptTemplate.from_template(self.prompt_text)
        self.model = chat_model
        self.chain = self._init_chain()

        # 混合检索配置
        hybrid_config = chroma_conf.get('hybrid_retrieval', )
        if use_hybrid is None:
            self.use_hybrid = hybrid_config.get('enabled', True)
        else:
            self.use_hybrid = use_hybrid

        # 初始化混合检索器
        if self.use_hybrid:
            self.hybrid_retriever = HybridRetriever(user_role=user_role)
            logger.info("混合检索已启用")
        else:
            self.hybrid_retriever = None
            logger.info("使用纯向量检索")

        # Query 改写配置
        query_rewrite_config = chroma_conf.get('query_rewrite', {})
        if use_query_rewrite is None:
            self.use_query_rewrite = query_rewrite_config.get('enabled', True)
        else:
            self.use_query_rewrite = use_query_rewrite

        # 初始化 Query 改写器
        if self.use_query_rewrite:
            self.query_rewriter = create_query_rewriter(
                chat_model=self.model,
                config=query_rewrite_config
            )
            logger.info(f"Query 改写已启用: history_turns={query_rewrite_config.get('history_turns', 3)}")
        else:
            self.query_rewriter = None
            logger.info("Query 改写未启用")

        # Reranker 配置
        reranker_config = chroma_conf.get('reranker', {})
        if use_reranker is None:
            self.use_reranker = reranker_config.get('enabled', True)
        else:
            self.use_reranker = use_reranker

        # 初始化 Reranker（只在混合检索启用时有效）
        if self.use_reranker and self.use_hybrid:
            from rag.reranker import create_reranker
            self.reranker = create_reranker(reranker_config)
            if self.reranker:
                logger.info(f"Reranker 已启用: model={self.reranker.model}")
            else:
                logger.info("Reranker 配置禁用")
        else:
            self.reranker = None
            if self.use_reranker and not self.use_hybrid:
                logger.info("Reranker 仅在混合检索模式下有效，当前为纯向量检索")
            else:
                logger.info("Reranker 未启用")

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

        # 权限默认拒绝：无权限时直接返回空结果
        if not accessible_space_ids:
            logger.warning(f"角色 {self.user_role} 无可访问空间，拒绝检索")
            return [], []

        # 获取当前有效的版本ID列表（防止旧版本占据Top-K）
        valid_version_ids = DocumentService.get_current_version_ids(accessible_space_ids)
        if not valid_version_ids:
            logger.info(f"知识空间 {accessible_space_ids} 无有效版本")
            return [], []

        # 构建过滤条件：知识空间 + 当前版本 + is_active
        filter_dict = {
            "$and": [
                {"space_id": {"$in": accessible_space_ids}},
                {"document_version_id": {"$in": valid_version_ids}},
                {"is_active": {"$eq": True}}
            ]
        }

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
        - 向量候选：使用距离阈值 (RELEVANCE_THRESHOLD)
        - BM25-only 候选：检查是否有有效的 BM25 分数，但不直接放行
        :param docs: 文档列表
        :param scores: 距离分数列表
        :return: (过滤后文档, 过滤后分数, 详细信息列表)
        """
        filtered_docs = []
        filtered_scores = []
        details = []

        for doc, distance in zip(docs, scores):
            # BM25-only 候选（没有向量距离）
            if distance is None:
                # BM25-only 候选需要满足：
                # 1. 在混合检索中被 RRF 融合选中
                # 2. 通过了权限和版本过滤
                # 3. 有明确的 BM25 关键词命中
                # 注意：不能直接放行所有 BM25-only，否则库外问题也会通过

                # 从 metadata 或 extra_fields 获取 BM25 分数（如果有）
                bm25_score = doc.metadata.get('bm25_score')

                # BM25-only 候选的保守策略：
                # - 如果 BM25 分数存在且 > 0，说明有关键词命中，保留
                # - 但这些候选没有语义相关性保证，可能是关键词误匹配
                # - 最终依赖 LLM 的"无法确认"判断
                if bm25_score is not None and bm25_score > 0:
                    filtered_docs.append(doc)
                    filtered_scores.append(distance)
                    detail = {
                        "raw_distance": None,
                        "relevance_score": None,
                        "bm25_score": bm25_score,
                        "source_name": doc.metadata.get("source_name", "未知"),
                        "passed": True,
                        "reason": "bm25_only_with_score"
                    }
                else:
                    # BM25 分数无效或为0，拒绝
                    detail = {
                        "raw_distance": None,
                        "relevance_score": None,
                        "bm25_score": bm25_score,
                        "source_name": doc.metadata.get("source_name", "未知"),
                        "passed": False,
                        "reason": "bm25_no_valid_score"
                    }
                details.append(detail)
                continue

            # 向量候选：使用距离阈值
            relevance_score = self.normalize_score(distance)

            detail = {
                "raw_distance": distance,
                "relevance_score": relevance_score,
                "source_name": doc.metadata.get("source_name", "未知"),
                "passed": distance <= self.RELEVANCE_THRESHOLD,
                "reason": "vector_threshold"
            }
            details.append(detail)

            if distance <= self.RELEVANCE_THRESHOLD:
                filtered_docs.append(doc)
                filtered_scores.append(distance)

        return filtered_docs, filtered_scores, details

    def validate_documents(self, docs: List[Document], scores: List[float]) -> Tuple[List[Document], List[float], str]:
        """
        验证文档版本有效性（文档和分数成对处理）
        过滤掉已下架、删除或非当前版本的文档
        :param docs: 文档列表
        :param scores: 距离分数列表
        :return: (有效文档列表, 有效分数列表, 拒答原因)
        """
        valid_docs = []
        valid_scores = []

        for doc, score in zip(docs, scores):
            metadata = doc.metadata
            document_id = metadata.get("document_id")
            version_id = metadata.get("document_version_id")

            if not document_id or not version_id:
                # 旧数据可能没有这些字段，暂时保留
                valid_docs.append(doc)
                valid_scores.append(score)
                continue

            # 检查版本是否存在
            version_info = DocumentService.get_version_by_id(version_id)
            if not version_info:
                logger.warning(f"版本{version_id}不存在，跳过")
                continue

            # 检查版本状态：必须是success
            if version_info.status != 'success':
                logger.info(f"版本{version_id}状态={version_info.status}，跳过")
                continue

            # 检查文档状态：不能是deleted
            document_info = DocumentService.get_document_by_id(document_id)
            if not document_info or document_info.is_deleted:
                logger.info(f"文档{document_id}已删除，跳过")
                continue

            # 检查是否为当前版本（通过 current_version_id 判断）
            if document_info.current_version_id != version_id:
                logger.info(f"版本{version_id}不是当前版本（当前={document_info.current_version_id}），跳过")
                continue

            valid_docs.append(doc)
            valid_scores.append(score)

        if not valid_docs:
            return [], [], "invalid_document_version"

        return valid_docs, valid_scores, ""

    def format_references(self, docs: List[Document], scores: List[float],
                         extra_fields: Optional[List[Dict]] = None) -> List[Dict]:
        """
        格式化引用信息（去重、完整字段）
        去重策略：
        1. 同一分片去重（version_id + chunk_index）
        2. 同一文档聚合（document_id）：只保留相关度最高的分片
        :param docs: 文档列表
        :param scores: 距离分数列表
        :param extra_fields: 额外字段列表（用于混合检索，包含 bm25_score 等）
        :return: 引用信息列表
        """
        references = []
        seen_chunks = set()  # 去重：同一分片只显示一次
        doc_best_chunks = {}  # 聚合：记录每个文档的最佳分片
        orphan_chunks = []  # 没有 document_id 的文档（测试数据或旧数据）

        # 第一遍：找出每个文档的最佳分片
        for idx, (doc, distance) in enumerate(zip(docs, scores)):
            metadata = doc.metadata
            document_id = metadata.get('document_id')
            version_id = metadata.get('document_version_id')
            chunk_index = metadata.get('chunk_index', 0)

            # 构建唯一标识
            chunk_key = f"{version_id}_{chunk_index}"

            # 跳过重复分片（同一版本同一分片）
            if chunk_key in seen_chunks:
                continue

            # 有 document_id：按文档聚合
            if document_id:
                # 如果该文档已经有候选分片，比较相关度
                if document_id in doc_best_chunks:
                    existing = doc_best_chunks[document_id]
                    existing_distance = existing['distance']

                    # 比较相关度（距离越小越好，None 视为无穷大）
                    if distance is not None and existing_distance is not None:
                        if distance < existing_distance:
                            # 当前分片更好，替换
                            doc_best_chunks[document_id] = {
                                'doc': doc,
                                'distance': distance,
                                'idx': idx,
                                'chunk_key': chunk_key
                            }
                    elif distance is not None and existing_distance is None:
                        # 当前有向量距离，更好
                        doc_best_chunks[document_id] = {
                            'doc': doc,
                            'distance': distance,
                            'idx': idx,
                            'chunk_key': chunk_key
                        }
                    # 如果当前 distance 为 None 且已有更好的，保持不变
                else:
                    # 首次遇到该文档
                    doc_best_chunks[document_id] = {
                        'doc': doc,
                        'distance': distance,
                        'idx': idx,
                        'chunk_key': chunk_key
                    }
            else:
                # 没有 document_id：视为独立分片，只做分片级去重
                orphan_chunks.append({
                    'doc': doc,
                    'distance': distance,
                    'idx': idx,
                    'chunk_key': chunk_key
                })
                seen_chunks.add(chunk_key)

        # 第二遍：构建引用列表
        reference_id = 1

        # 1. 有 document_id 的文档（每个文档只取最佳分片）
        for document_id, info in doc_best_chunks.items():
            doc = info['doc']
            distance = info['distance']
            idx = info['idx']
            chunk_key = info['chunk_key']

            # 标记已使用
            seen_chunks.add(chunk_key)

            # 构建引用信息
            ref = self._build_reference_dict(doc, distance, reference_id, idx, extra_fields)
            references.append(ref)
            reference_id += 1

        # 2. 没有 document_id 的孤儿分片（测试数据或旧数据）
        for orphan in orphan_chunks:
            doc = orphan['doc']
            distance = orphan['distance']
            idx = orphan['idx']

            # 构建引用信息
            ref = self._build_reference_dict(doc, distance, reference_id, idx, extra_fields)
            references.append(ref)
            reference_id += 1

        return references

    def _build_reference_dict(self, doc: Document, distance: float, reference_id: int,
                             original_idx: int, extra_fields: Optional[List[Dict]] = None) -> Dict:
        """
        构建单个引用的字典结构
        :param doc: 文档对象
        :param distance: 向量距离
        :param reference_id: 引用编号
        :param original_idx: 原始索引（用于获取 extra_fields）
        :param extra_fields: 额外字段列表
        :return: 引用字典
        """
        metadata = doc.metadata

        # 提取字段
        source_name = metadata.get('source_name', '未知来源')
        document_id = metadata.get('document_id')
        version_id = metadata.get('document_version_id')
        page_num = metadata.get('page', metadata.get('page_number'))
        chunk_index = metadata.get('chunk_index', 0)
        sheet_name = metadata.get('sheet_name')
        row_number = metadata.get('row_number')

        # 获取版本号和文件路径
        version_number = None
        file_path = None
        if version_id:
            version_info = DocumentService.get_version_by_id(version_id)
            if version_info:
                version_number = version_info.version_number
                file_path = version_info.file_path

        # 构建定位信息
        section_info = []
        if sheet_name and row_number:
            section_info.append(f"工作表“{sheet_name}”第{row_number}行")
        elif page_num is not None:
            section_info.append(f"第{page_num}页")
        else:
            # TXT文件没有页码，使用分片编号
            section_info.append(f"分片{chunk_index + 1}")

        section = "、".join(section_info) if section_info else f"分片{chunk_index + 1}"

        # 计算相似度分数（如果有向量距离）
        relevance_score = None if distance is None else self.normalize_score(distance)

        # 构建引用信息
        ref = {
            'reference_id': reference_id,
            'source_name': source_name,
            'document_id': document_id,
            'document_version_id': version_id,
            'version_number': version_number,
            'file_path': file_path,
            'page': page_num,
            'page_number': page_num,
            'sheet_name': sheet_name,
            'row_number': row_number,
            'section': section,
            'chunk_index': chunk_index,
            'relevance_score': round(relevance_score, 4) if relevance_score is not None else None,
            'raw_distance': round(distance, 4) if distance is not None else None,
            'content': doc.page_content[:300] + '...' if len(doc.page_content) > 300 else doc.page_content
        }

        # 添加混合检索的额外字段
        if extra_fields and original_idx < len(extra_fields):
            extra = extra_fields[original_idx]
            if 'bm25_score' in extra:
                ref['bm25_score'] = round(extra['bm25_score'], 4) if extra['bm25_score'] is not None else None
            if 'vector_rank' in extra:
                ref['vector_rank'] = extra['vector_rank']
            if 'bm25_rank' in extra:
                ref['bm25_rank'] = extra['bm25_rank']
            if 'rrf_score' in extra:
                ref['rrf_score'] = round(extra['rrf_score'], 4)
            if 'initial_rrf_rank' in extra:
                ref['initial_rrf_rank'] = extra['initial_rrf_rank']
            if 'rerank_rank' in extra:
                ref['rerank_rank'] = extra['rerank_rank']
            if 'rerank_score' in extra:
                ref['rerank_score'] = round(extra['rerank_score'], 4) if extra['rerank_score'] is not None else None
            if 'retrieval_channels' in extra:
                ref['retrieval_channels'] = extra['retrieval_channels']

        return ref

    def build_context_with_citations(self, docs: List[Document]) -> str:
        """
        构建带引用编号和文件名的上下文
        :param docs: 文档列表
        :return: 上下文文本
        """
        context_parts = []
        for idx, doc in enumerate(docs, 1):
            # 获取文件名
            source_name = doc.metadata.get('source_name', '未知文档')
            version_number = doc.metadata.get('version_number', '')

            # 构建完整的文件标识
            if version_number:
                file_identifier = f"{source_name}（版本{version_number}）"
            else:
                file_identifier = source_name

            context_parts.append(f"【参考资料{idx}：{file_identifier}】\n{doc.page_content}")

        return "\n\n".join(context_parts)

    def _hybrid_search(self, query: str, k: int = None, rerank_k: int = None) -> Tuple[List[Document], List[float], List[Dict], Dict]:
        """
        混合检索（向量 + BM25 + RRF 融合）
        :param query: 查询文本
        :param k: 最终返回数量（用于 Rerank 后）
        :param rerank_k: 送入 Rerank 的候选数（默认从配置读取）
        :return: (文档列表, 距离分数列表, 额外字段列表, 统计信息)
        """
        if k is None:
            k = chroma_conf.get("k", 3)

        # 确定 Rerank 前需要召回的候选数
        if rerank_k is None:
            rerank_config = chroma_conf.get('reranker', {})
            rerank_k = rerank_config.get('candidate_k', 10)

        try:
            # 调用混合检索器（获取 rerank_k 个候选）
            candidates, stats = self.hybrid_retriever.search(query, top_k=rerank_k)

            if not candidates:
                return [], [], [], stats

            # 转换为标准格式
            docs = []
            scores = []
            extra_fields = []

            for cand in candidates:
                docs.append(cand.document)
                scores.append(cand.vector_distance)  # 可能为 None（BM25-only）
                extra_fields.append({
                    'bm25_score': cand.bm25_score,
                    'vector_rank': cand.vector_rank,
                    'bm25_rank': cand.bm25_rank,
                    'rrf_score': cand.rrf_score,
                    'initial_rrf_rank': cand.initial_rrf_rank,
                    'retrieval_channels': cand.retrieval_channels
                })

            return docs, scores, extra_fields, stats

        except Exception as e:
            logger.error(f"混合检索失败：{str(e)}", exc_info=True)
            # 降级到纯向量检索
            logger.warning("混合检索失败，降级到纯向量检索")
            docs, scores = self.retriever_docs_with_scores(query, k)
            stats = {
                'vector_retrieved': len(docs),
                'bm25_retrieved': 0,
                'fused_count': len(docs),
                'retrieval_mode': 'vector_fallback',
                'vector_failed': False,
                'bm25_failed': True
            }
            return docs, scores, [], stats

    def rag_summarize(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> Dict:
        """
        RAG问答（带引用和相关性判断）
        支持混合检索模式和 Query 改写
        :param query: 用户问题
        :param history: 对话历史，格式：[{"role": "user", "content": "..."}, ...]
        :param on_token: 可选的生成片段回调；提供时使用模型流式生成
        :return: 包含答案、引用、耗时等信息的字典
        """
        start_time = time.time()

        # Query 改写结果初始化
        rewrite_result = None
        retrieval_query = query  # 默认使用原始问题检索

        try:
            # 0. Query 改写（如果启用）
            if self.use_query_rewrite and self.query_rewriter:
                rewrite_result = self.query_rewriter.rewrite(query, history)
                retrieval_query = rewrite_result.rewritten_query

                if rewrite_result.applied:
                    logger.info(f"Query 改写应用: '{query[:50]}' -> '{retrieval_query[:50]}' "
                               f"({rewrite_result.rewrite_time_ms}ms)")
                else:
                    logger.debug(f"Query 改写未应用: {rewrite_result.fallback_reason}")

            # 1. 检索文档和分数（使用改写后的问题）
            retrieval_start = time.time()

            # 根据配置选择检索模式
            if self.use_hybrid and self.hybrid_retriever:
                docs, scores, extra_fields, hybrid_stats = self._hybrid_search(retrieval_query)
                retrieval_mode = hybrid_stats.get('retrieval_mode', 'hybrid')
                vector_retrieved_count = hybrid_stats.get('vector_retrieved', 0)
                bm25_retrieved_count = hybrid_stats.get('bm25_retrieved', 0)
                fused_count = hybrid_stats.get('fused_count', 0)
            else:
                docs, scores = self.retriever_docs_with_scores(retrieval_query)
                extra_fields = []
                retrieval_mode = 'vector_only'
                vector_retrieved_count = len(docs)
                bm25_retrieved_count = 0
                fused_count = len(docs)

            retrieval_time_ms = int((time.time() - retrieval_start) * 1000)

            # 记录检索结果
            logger.info(f"检索模式={retrieval_mode}, 向量={vector_retrieved_count}, BM25={bm25_retrieved_count}, 融合={fused_count}")

            # 2. Query 改写回退逻辑：如果改写后无结果且改写确实发生了，用原始问题重试
            if not docs and rewrite_result and rewrite_result.applied and retrieval_query != query:
                logger.warning(f"改写后无结果，使用原始问题重试: '{query[:50]}'")

                # 使用原始问题重试
                retrieval_start_fallback = time.time()
                if self.use_hybrid and self.hybrid_retriever:
                    docs, scores, extra_fields, hybrid_stats = self._hybrid_search(query)
                    retrieval_mode = hybrid_stats.get('retrieval_mode', 'hybrid') + '_fallback'
                    vector_retrieved_count = hybrid_stats.get('vector_retrieved', 0)
                    bm25_retrieved_count = hybrid_stats.get('bm25_retrieved', 0)
                    fused_count = hybrid_stats.get('fused_count', 0)
                else:
                    docs, scores = self.retriever_docs_with_scores(query)
                    extra_fields = []
                    retrieval_mode = 'vector_only_fallback'
                    vector_retrieved_count = len(docs)
                    bm25_retrieved_count = 0
                    fused_count = len(docs)

                retrieval_time_ms += int((time.time() - retrieval_start_fallback) * 1000)

                # 更新 rewrite_result 以记录回退
                if rewrite_result:
                    rewrite_result.fallback_reason = 'rewrite_no_result'
                    rewrite_result.applied = False

                logger.info(f"原始问题检索: 模式={retrieval_mode}, 向量={vector_retrieved_count}, "
                           f"BM25={bm25_retrieved_count}, 融合={fused_count}")

            # 3. 检查是否有检索结果
            if not docs:
                logger.info(f"无检索结果，拒答：{query[:50]}")

                result = {
                    "answer": self.REFUSAL_MESSAGE,
                    "has_answer": False,
                    "references": [],
                    "retrieved_count": 0,
                    "filtered_count": 0,
                    "retrieval_time_ms": retrieval_time_ms,
                    "generation_time_ms": 0,
                    "total_time_ms": int((time.time() - start_time) * 1000),
                    "refusal_reason": "no_retrieval_result",
                    "retrieval_mode": retrieval_mode,
                    "vector_retrieved_count": vector_retrieved_count,
                    "bm25_retrieved_count": bm25_retrieved_count,
                    "fused_count": fused_count,
                    "original_query": query,
                    # Reranker 字段（未执行）
                    "rerank_enabled": self.use_reranker and self.use_hybrid,
                    "rerank_applied": False,
                    "rerank_model": None,
                    "rerank_candidate_count": 0,
                    "rerank_output_count": 0,
                    "rerank_time_ms": 0,
                    "rerank_fallback_reason": "no_candidates"
                }

                # 添加 Query 改写字段
                if rewrite_result:
                    result.update({
                        "retrieval_query": retrieval_query,
                        "query_rewrite_applied": rewrite_result.applied,
                        "query_rewrite_fallback_reason": rewrite_result.fallback_reason,
                        "query_rewrite_time_ms": rewrite_result.rewrite_time_ms
                    })

                return result

            # 3. 按相关性阈值过滤（只对有向量距离的文档应用）
            filtered_docs, filtered_scores, filter_details = self.filter_by_relevance(docs, scores)

            logger.info(f"阈值过滤后剩余{len(filtered_docs)}个文档")
            for detail in filter_details:
                logger.debug(f"  - {detail['source_name']}: distance={detail['raw_distance']}, "
                           f"relevance={detail['relevance_score']}, passed={detail['passed']}")

            # 4. 检查是否低于阈值
            if not filtered_docs:
                logger.info(f"所有文档低于相关性阈值，拒答：{query[:50]}")

                result = {
                    "answer": self.REFUSAL_MESSAGE,
                    "has_answer": False,
                    "references": [],
                    "retrieved_count": len(docs),
                    "filtered_count": 0,
                    "retrieval_time_ms": retrieval_time_ms,
                    "generation_time_ms": 0,
                    "total_time_ms": int((time.time() - start_time) * 1000),
                    "refusal_reason": "below_relevance_threshold",
                    "retrieval_mode": retrieval_mode,
                    "vector_retrieved_count": vector_retrieved_count,
                    "bm25_retrieved_count": bm25_retrieved_count,
                    "fused_count": fused_count,
                    "original_query": query,
                    # Reranker 字段（未执行）
                    "rerank_enabled": self.use_reranker and self.use_hybrid,
                    "rerank_applied": False,
                    "rerank_model": None,
                    "rerank_candidate_count": 0,
                    "rerank_output_count": 0,
                    "rerank_time_ms": 0,
                    "rerank_fallback_reason": "filtered_out"
                }

                # 添加 Query 改写字段
                if rewrite_result:
                    result.update({
                        "retrieval_query": retrieval_query,
                        "query_rewrite_applied": rewrite_result.applied,
                        "query_rewrite_fallback_reason": rewrite_result.fallback_reason,
                        "query_rewrite_time_ms": rewrite_result.rewrite_time_ms
                    })

                return result

            # 5. 验证文档版本有效性（文档和分数成对处理）
            valid_docs, valid_scores, invalid_reason = self.validate_documents(filtered_docs, filtered_scores)

            # 同步过滤 extra_fields，保持对齐
            valid_extra_fields = []
            if extra_fields and filtered_docs:
                # 构建 filtered_docs 的索引映射
                filtered_indices = []
                for i, (filtered_doc, filtered_score) in enumerate(zip(filtered_docs, filtered_scores)):
                    # 在原始 docs 中找到对应的索引
                    for j, (doc, score) in enumerate(zip(docs, scores)):
                        if (doc.page_content == filtered_doc.page_content and
                            doc.metadata.get('document_version_id') == filtered_doc.metadata.get('document_version_id') and
                            doc.metadata.get('chunk_index') == filtered_doc.metadata.get('chunk_index')):
                            filtered_indices.append(j)
                            break

                # 进一步过滤：只保留 valid_docs 对应的 extra_fields
                valid_indices = []
                for valid_doc, valid_score in zip(valid_docs, valid_scores):
                    for i, (filtered_doc, filtered_score) in enumerate(zip(filtered_docs, filtered_scores)):
                        if (valid_doc.page_content == filtered_doc.page_content and
                            valid_doc.metadata.get('document_version_id') == filtered_doc.metadata.get('document_version_id') and
                            valid_doc.metadata.get('chunk_index') == filtered_doc.metadata.get('chunk_index')):
                            if i < len(filtered_indices):
                                valid_indices.append(filtered_indices[i])
                            break

                # 根据索引提取 valid_extra_fields
                for idx in valid_indices:
                    if idx < len(extra_fields):
                        valid_extra_fields.append(extra_fields[idx])

            if not valid_docs:
                logger.info(f"所有文档版本无效，拒答：{query[:50]}")

                result = {
                    "answer": self.REFUSAL_MESSAGE,
                    "has_answer": False,
                    "references": [],
                    "retrieved_count": len(docs),
                    "filtered_count": len(filtered_docs),
                    "retrieval_time_ms": retrieval_time_ms,
                    "generation_time_ms": 0,
                    "total_time_ms": int((time.time() - start_time) * 1000),
                    "refusal_reason": invalid_reason,
                    "retrieval_mode": retrieval_mode,
                    "vector_retrieved_count": vector_retrieved_count,
                    "bm25_retrieved_count": bm25_retrieved_count,
                    "fused_count": fused_count,
                    "original_query": query,
                    # Reranker 字段（未执行）
                    "rerank_enabled": self.use_reranker and self.use_hybrid,
                    "rerank_applied": False,
                    "rerank_model": None,
                    "rerank_candidate_count": 0,
                    "rerank_output_count": 0,
                    "rerank_time_ms": 0,
                    "rerank_fallback_reason": "invalid_documents"
                }

                # 添加 Query 改写字段
                if rewrite_result:
                    result.update({
                        "retrieval_query": retrieval_query,
                        "query_rewrite_applied": rewrite_result.applied,
                        "query_rewrite_fallback_reason": rewrite_result.fallback_reason,
                        "query_rewrite_time_ms": rewrite_result.rewrite_time_ms
                    })

                return result

            # 6. Rerank 精排（如果启用且混合检索）
            rerank_applied = False
            rerank_model = None
            rerank_candidate_count = 0
            rerank_output_count = 0
            rerank_time_ms = 0
            rerank_fallback_reason = None

            if self.use_reranker and self.reranker and self.use_hybrid:
                from rag.hybrid_retriever import RetrievalCandidate

                # 将 valid_docs 转换为 RetrievalCandidate 对象
                rerank_candidates = []
                for i, (doc, score) in enumerate(zip(valid_docs, valid_scores)):
                    cand = RetrievalCandidate(
                        document=doc,
                        chunk_key=RetrievalCandidate.get_chunk_key(doc)
                    )

                    # 填充已有字段
                    if valid_extra_fields and i < len(valid_extra_fields):
                        extra = valid_extra_fields[i]
                        cand.vector_distance = score
                        cand.vector_rank = extra.get('vector_rank')
                        cand.relevance_score = self.normalize_score(score) if score is not None else None
                        cand.bm25_score = extra.get('bm25_score')
                        cand.bm25_rank = extra.get('bm25_rank')
                        cand.rrf_score = extra.get('rrf_score', 0.0)
                        cand.initial_rrf_rank = extra.get('initial_rrf_rank')
                        cand.retrieval_channels = extra.get('retrieval_channels', [])

                    rerank_candidates.append(cand)

                # 调用 Reranker（使用实际产生候选的检索问题）
                rerank_outcome = self.reranker.rerank(
                    query=retrieval_query,  # 使用实际检索问题
                    candidates=rerank_candidates
                )

                # 更新统计信息
                rerank_applied = rerank_outcome.applied
                rerank_model = rerank_outcome.model
                rerank_candidate_count = rerank_outcome.input_count
                rerank_output_count = rerank_outcome.output_count
                rerank_time_ms = rerank_outcome.rerank_time_ms
                rerank_fallback_reason = rerank_outcome.fallback_reason

                if rerank_applied:
                    logger.info(f"Rerank 应用成功：model={rerank_model}, "
                               f"input={rerank_candidate_count}, output={rerank_output_count}, "
                               f"time={rerank_time_ms:.1f}ms")

                    # 使用精排后的结果
                    valid_docs = [c.document for c in rerank_outcome.candidates]
                    valid_scores = [c.vector_distance for c in rerank_outcome.candidates]
                    valid_extra_fields = [c.to_dict() for c in rerank_outcome.candidates]
                else:
                    logger.info(f"Rerank 未应用：{rerank_fallback_reason}")
                    # 降级：仍使用 rerank_outcome 中返回的候选（已截断为 Top 3）
                    if rerank_outcome.candidates:
                        valid_docs = [c.document for c in rerank_outcome.candidates]
                        valid_scores = [c.vector_distance for c in rerank_outcome.candidates]
                        valid_extra_fields = [c.to_dict() for c in rerank_outcome.candidates]

            # 7. 构建上下文
            context = self.build_context_with_citations(valid_docs)

            # 8. 生成答案（使用原始问题，不是改写后的问题）
            generation_start = time.time()
            generation_input = {
                "input": query,  # 使用原始问题
                "context": context,
            }
            if on_token is None:
                answer = self.chain.invoke(generation_input)
            else:
                answer_chunks = []
                for chunk in self.chain.stream(generation_input):
                    if not chunk:
                        continue
                    answer_chunks.append(chunk)
                    on_token(chunk)
                answer = "".join(answer_chunks)
            generation_time_ms = int((time.time() - generation_start) * 1000)

            # 9. 格式化引用
            references = self.format_references(valid_docs, valid_scores, valid_extra_fields)

            total_time_ms = int((time.time() - start_time) * 1000)

            logger.info(f"RAG问答成功：query='{query[:50]}...', mode={retrieval_mode}, "
                       f"retrieved={len(docs)}, filtered={len(filtered_docs)}, valid={len(valid_docs)}, "
                       f"rerank_applied={rerank_applied}, "
                       f"retrieval={retrieval_time_ms}ms, generation={generation_time_ms}ms, total={total_time_ms}ms")

            result = {
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
                "model_name": getattr(self.model, "model_name", "unknown"),
                "retrieval_mode": retrieval_mode,
                "vector_retrieved_count": vector_retrieved_count,
                "bm25_retrieved_count": bm25_retrieved_count,
                "fused_count": fused_count,
                "original_query": query,
                # Reranker 字段
                "rerank_enabled": self.use_reranker and self.use_hybrid,
                "rerank_applied": rerank_applied,
                "rerank_model": rerank_model,
                "rerank_candidate_count": rerank_candidate_count,
                "rerank_output_count": rerank_output_count,
                "rerank_time_ms": round(rerank_time_ms, 1),
                "rerank_fallback_reason": rerank_fallback_reason
            }

            # 添加 Query 改写字段
            if rewrite_result:
                result.update({
                    "retrieval_query": retrieval_query,
                    "query_rewrite_applied": rewrite_result.applied,
                    "query_rewrite_fallback_reason": rewrite_result.fallback_reason,
                    "query_rewrite_time_ms": rewrite_result.rewrite_time_ms
                })

            return result

        except Exception as e:
            logger.error(f"RAG问答失败：{str(e)}", exc_info=True)
            total_time_ms = int((time.time() - start_time) * 1000)

            # 不向用户暴露堆栈信息
            result = {
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
                "error_internal": str(e),  # 仅用于内部日志
                "original_query": query,
                # Reranker 字段（未执行）
                "rerank_enabled": False,
                "rerank_applied": False,
                "rerank_model": None,
                "rerank_candidate_count": 0,
                "rerank_output_count": 0,
                "rerank_time_ms": 0,
                "rerank_fallback_reason": "system_error"
            }

            # 添加 Query 改写字段
            if rewrite_result:
                result.update({
                    "retrieval_query": retrieval_query,
                    "query_rewrite_applied": rewrite_result.applied,
                    "query_rewrite_fallback_reason": rewrite_result.fallback_reason,
                    "query_rewrite_time_ms": rewrite_result.rewrite_time_ms
                })

            return result

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
