"""
混合检索器：向量检索 + BM25 关键词检索 + RRF 融合
"""
from typing import List, Dict, Tuple, Optional
from langchain_core.documents import Document
from rag.bm25_retriever import BM25Retriever
from rag.vector_store import VectorStoreService
from services.permission_service import PermissionService
from services.document_service import DocumentService
from utils.logger_handler import logger
from utils.config_handler import chroma_conf


class RetrievalCandidate:
    """检索候选对象（统一表示）"""

    def __init__(self, document: Document, chunk_key: str):
        """
        初始化候选
        :param document: 文档对象
        :param chunk_key: 唯一标识 (document_version_id_chunk_index)
        """
        self.document = document
        self.chunk_key = chunk_key

        # 向量检索相关
        self.vector_distance: Optional[float] = None
        self.vector_rank: Optional[int] = None
        self.relevance_score: Optional[float] = None

        # BM25 检索相关
        self.bm25_score: Optional[float] = None
        self.bm25_rank: Optional[int] = None

        # RRF 融合
        self.rrf_score: float = 0.0
        self.initial_rrf_rank: Optional[int] = None  # RRF 初始排名

        # Rerank 精排
        self.rerank_rank: Optional[int] = None
        self.rerank_score: Optional[float] = None

        # 检索通道标识
        self.retrieval_channels: List[str] = []

    @staticmethod
    def get_chunk_key(doc: Document) -> str:
        """
        生成分片唯一标识
        :param doc: 文档
        :return: 唯一标识字符串
        """
        version_id = doc.metadata.get('document_version_id', 'unknown')
        chunk_index = doc.metadata.get('chunk_index', 0)
        return f"{version_id}_{chunk_index}"

    def add_vector_result(self, distance: float, rank: int, relevance_score: float):
        """添加向量检索结果"""
        self.vector_distance = distance
        self.vector_rank = rank
        self.relevance_score = relevance_score
        if 'vector' not in self.retrieval_channels:
            self.retrieval_channels.append('vector')

    def add_bm25_result(self, score: float, rank: int):
        """添加 BM25 检索结果"""
        self.bm25_score = score
        self.bm25_rank = rank
        if 'bm25' not in self.retrieval_channels:
            self.retrieval_channels.append('bm25')

    def calculate_rrf_score(self, rrf_constant: int = 60):
        """
        计算 RRF 分数
        :param rrf_constant: RRF 常数（默认 60）
        """
        self.rrf_score = 0.0

        if self.vector_rank is not None:
            self.rrf_score += 1.0 / (rrf_constant + self.vector_rank)

        if self.bm25_rank is not None:
            self.rrf_score += 1.0 / (rrf_constant + self.bm25_rank)

    def to_dict(self) -> Dict:
        """转换为字典（用于返回）"""
        return {
            'document': self.document,
            'vector_distance': self.vector_distance,
            'vector_rank': self.vector_rank,
            'relevance_score': self.relevance_score,
            'bm25_score': self.bm25_score,
            'bm25_rank': self.bm25_rank,
            'rrf_score': self.rrf_score,
            'initial_rrf_rank': self.initial_rrf_rank,
            'rerank_rank': self.rerank_rank,
            'rerank_score': self.rerank_score,
            'retrieval_channels': self.retrieval_channels
        }


class HybridRetriever:
    """混合检索器"""

    def __init__(self, user_role: str = None):
        """
        初始化混合检索器
        :param user_role: 用户角色
        """
        self.user_role = user_role
        self.vector_store = VectorStoreService()

        # 读取配置
        hybrid_config = chroma_conf.get('hybrid_retrieval', {})
        self.vector_candidate_k = hybrid_config.get('vector_candidate_k', 10)
        self.bm25_candidate_k = hybrid_config.get('bm25_candidate_k', 10)
        self.final_top_k = hybrid_config.get('final_top_k', 3)
        self.rrf_constant = hybrid_config.get('rrf_constant', 60)

    def search(self, query: str, top_k: int = None) -> Tuple[List[RetrievalCandidate], Dict]:
        """
        混合检索主入口
        :param query: 查询文本
        :param top_k: 最终返回数量（覆盖配置）
        :return: (候选列表, 统计信息)
        """
        if top_k is None:
            top_k = self.final_top_k

        stats = {
            'vector_retrieved': 0,
            'bm25_retrieved': 0,
            'fused_count': 0,
            'retrieval_mode': 'hybrid',
            'vector_failed': False,
            'bm25_failed': False
        }

        # 1. 权限和版本过滤
        accessible_space_ids, valid_version_ids = self._get_access_scope()

        if not accessible_space_ids:
            logger.warning(f"角色 {self.user_role} 无可访问空间，拒绝检索")
            return [], stats

        if not valid_version_ids:
            logger.info(f"知识空间 {accessible_space_ids} 无有效版本")
            return [], stats

        # 2. 向量检索
        vector_candidates = self._vector_search(
            query, accessible_space_ids, valid_version_ids, self.vector_candidate_k
        )
        stats['vector_retrieved'] = len(vector_candidates)

        # 3. BM25 检索（使用相同的权限和版本过滤）
        bm25_candidates = self._bm25_search(
            query, accessible_space_ids, valid_version_ids, self.bm25_candidate_k
        )
        stats['bm25_retrieved'] = len(bm25_candidates)

        # 4. RRF 融合
        fused_candidates = self._rrf_fusion(vector_candidates, bm25_candidates)
        stats['fused_count'] = len(fused_candidates)

        # 5. 取 Top K
        final_candidates = fused_candidates[:top_k]

        logger.info(f"混合检索完成：query='{query[:50]}', "
                   f"vector={stats['vector_retrieved']}, bm25={stats['bm25_retrieved']}, "
                   f"fused={stats['fused_count']}, final={len(final_candidates)}")

        return final_candidates, stats

    def _get_access_scope(self) -> Tuple[List[int], List[int]]:
        """
        获取访问范围（权限和当前版本）
        :return: (accessible_space_ids, valid_version_ids)
        """
        # 获取可访问知识空间
        accessible_space_ids = PermissionService.get_accessible_space_ids(self.user_role) if self.user_role else []

        if not accessible_space_ids:
            return [], []

        # 获取当前有效版本
        valid_version_ids = DocumentService.get_current_version_ids(accessible_space_ids)

        return accessible_space_ids, valid_version_ids

    def _vector_search(self, query: str, space_ids: List[int],
                      version_ids: List[int], k: int) -> List[Dict]:
        """
        向量检索
        :param query: 查询文本
        :param space_ids: 可访问空间 ID
        :param version_ids: 有效版本 ID
        :param k: 返回数量
        :return: 候选列表
        """
        try:
            # 构建过滤条件
            filter_dict = {
                "$and": [
                    {"space_id": {"$in": space_ids}},
                    {"document_version_id": {"$in": version_ids}},
                    {"is_active": {"$eq": True}}
                ]
            }

            # 向量检索
            results = self.vector_store.vector_store.similarity_search_with_score(
                query=query,
                k=k,
                filter=filter_dict
            )

            if not results:
                return []

            # 转换为候选格式
            candidates = []
            for rank, (doc, distance) in enumerate(results, start=1):
                # 计算相关度分数
                import math
                relevance_score = math.exp(-distance)

                candidates.append({
                    'document': doc,
                    'vector_distance': distance,
                    'vector_rank': rank,
                    'relevance_score': relevance_score
                })

            return candidates

        except Exception as e:
            logger.error(f"向量检索失败：{str(e)}", exc_info=True)
            return []

    def _bm25_search(self, query: str, space_ids: List[int],
                    version_ids: List[int], k: int) -> List[Dict]:
        """
        BM25 检索
        :param query: 查询文本
        :param space_ids: 可访问空间 ID
        :param version_ids: 有效版本 ID
        :param k: 返回数量
        :return: 候选列表
        """
        try:
            # 获取所有符合权限和版本的文档
            corpus_docs = self._get_bm25_corpus(space_ids, version_ids)

            if not corpus_docs:
                logger.info("BM25 语料为空")
                return []

            # 构建 BM25 索引并检索
            bm25_retriever = BM25Retriever(corpus_docs)
            candidates = bm25_retriever.search(query, top_k=k)

            return candidates

        except Exception as e:
            logger.error(f"BM25 检索失败：{str(e)}", exc_info=True)
            return []

    def _get_bm25_corpus(self, space_ids: List[int], version_ids: List[int]) -> List[Document]:
        """
        获取 BM25 语料（从 Chroma 中读取符合条件的文档）
        :param space_ids: 可访问空间 ID
        :param version_ids: 有效版本 ID
        :return: 文档列表
        """
        try:
            # 构建过滤条件
            filter_dict = {
                "$and": [
                    {"space_id": {"$in": space_ids}},
                    {"document_version_id": {"$in": version_ids}},
                    {"is_active": {"$eq": True}}
                ]
            }

            # 从 Chroma 获取所有符合条件的文档
            # 注意：这里我们获取尽可能多的文档作为 BM25 语料
            # 使用一个较大的 k 值来获取所有候选
            all_docs = self.vector_store.vector_store.get(
                where=filter_dict,
                limit=10000  # 限制最大数量，避免内存问题
            )

            if not all_docs or 'documents' not in all_docs:
                return []

            # 转换为 Document 对象
            corpus = []
            documents = all_docs.get('documents', [])
            metadatas = all_docs.get('metadatas', [])

            for doc_text, metadata in zip(documents, metadatas):
                # 过滤：必须有 document_id, document_version_id, chunk_index
                if not metadata.get('document_id'):
                    continue
                if not metadata.get('document_version_id'):
                    continue
                if metadata.get('chunk_index') is None:
                    continue

                corpus.append(Document(page_content=doc_text, metadata=metadata))

            logger.info(f"BM25 语料构建完成：{len(corpus)} 个分片")
            return corpus

        except Exception as e:
            logger.error(f"获取 BM25 语料失败：{str(e)}", exc_info=True)
            return []

    def _rrf_fusion(self, vector_candidates: List[Dict],
                   bm25_candidates: List[Dict]) -> List[RetrievalCandidate]:
        """
        RRF 融合
        :param vector_candidates: 向量候选
        :param bm25_candidates: BM25 候选
        :return: 融合后的候选列表（按 RRF 分数降序）
        """
        # 使用字典管理候选（chunk_key -> RetrievalCandidate）
        candidate_map = {}

        # 添加向量候选
        for v_cand in vector_candidates:
            doc = v_cand['document']
            chunk_key = RetrievalCandidate.get_chunk_key(doc)

            if chunk_key not in candidate_map:
                candidate_map[chunk_key] = RetrievalCandidate(doc, chunk_key)

            candidate_map[chunk_key].add_vector_result(
                distance=v_cand['vector_distance'],
                rank=v_cand['vector_rank'],
                relevance_score=v_cand['relevance_score']
            )

        # 添加 BM25 候选
        for b_cand in bm25_candidates:
            doc = b_cand['document']
            chunk_key = RetrievalCandidate.get_chunk_key(doc)

            if chunk_key not in candidate_map:
                candidate_map[chunk_key] = RetrievalCandidate(doc, chunk_key)

            candidate_map[chunk_key].add_bm25_result(
                score=b_cand['bm25_score'],
                rank=b_cand['bm25_rank']
            )

        # 计算 RRF 分数
        for candidate in candidate_map.values():
            candidate.calculate_rrf_score(self.rrf_constant)

        # 转换为列表并排序
        candidates = list(candidate_map.values())

        # 排序：按 RRF 分数降序，分数相同时按向量排名、BM25 排名、chunk_key
        candidates.sort(
            key=lambda c: (
                -c.rrf_score,  # RRF 分数降序
                c.vector_rank if c.vector_rank is not None else 999999,  # 向量排名升序
                c.bm25_rank if c.bm25_rank is not None else 999999,  # BM25 排名升序
                c.chunk_key  # 稳定排序
            )
        )

        # 记录初始 RRF 排名
        for rank, candidate in enumerate(candidates, start=1):
            candidate.initial_rrf_rank = rank

        return candidates


if __name__ == '__main__':
    # 测试
    retriever = HybridRetriever(user_role='employee')
    candidates, stats = retriever.search("扫地机器人价格")

    print(f"检索统计：{stats}")
    for cand in candidates:
        print(f"RRF={cand.rrf_score:.4f}, channels={cand.retrieval_channels}, "
              f"content={cand.document.page_content[:50]}")
