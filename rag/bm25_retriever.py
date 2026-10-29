"""
BM25 关键词检索器
提供中文分词、文本规范化和 BM25 检索功能
"""
import re
import jieba
from typing import List, Dict, Tuple
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi
from utils.logger_handler import logger


class BM25Retriever:
    """BM25 关键词检索器"""

    def __init__(self, documents: List[Document]):
        """
        初始化 BM25 检索器
        :param documents: 文档列表（必须已通过权限和版本过滤）
        """
        self.documents = documents
        self.tokenized_corpus = []
        self.bm25 = None

        if documents:
            self._build_index()

    def _normalize_text(self, text: str) -> str:
        """
        文本规范化
        :param text: 原始文本
        :return: 规范化后的文本
        """
        if not text:
            return ""

        # 统一转小写（英文）
        text = text.lower()

        # 保留产品型号、故障码、版本号等
        # 这些模式会被作为整体保留
        # 例如: KB-R2, E03, 220V, HTTP-500, V5.2
        # 在分词时会尽量保留这些模式

        return text

    def _tokenize(self, text: str) -> List[str]:
        """
        中文分词和规范化
        :param text: 文本
        :return: 分词列表
        """
        # 规范化文本
        normalized_text = self._normalize_text(text)

        # 使用 jieba 搜索模式分词（更细粒度，提高召回）
        tokens = jieba.cut_for_search(normalized_text)

        # 过滤空白和单字符（保留数字和字母单字符）
        filtered_tokens = []
        for token in tokens:
            token = token.strip()
            if not token:
                continue

            # 保留多字符词
            if len(token) > 1:
                filtered_tokens.append(token)
            # 保留单个数字或字母（用于型号、版本号）
            elif token.isalnum():
                filtered_tokens.append(token)

        return filtered_tokens

    def _build_index(self):
        """构建 BM25 索引"""
        try:
            # 对每个文档进行分词
            for doc in self.documents:
                tokens = self._tokenize(doc.page_content)
                self.tokenized_corpus.append(tokens)

            # 构建 BM25 索引
            if self.tokenized_corpus:
                self.bm25 = BM25Okapi(self.tokenized_corpus)
                logger.info(f"BM25 索引构建完成，共 {len(self.documents)} 个文档")
            else:
                logger.warning("BM25 索引为空，所有文档分词结果为空")

        except Exception as e:
            logger.error(f"BM25 索引构建失败：{str(e)}", exc_info=True)
            raise

    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        BM25 检索
        :param query: 查询文本
        :param top_k: 返回数量
        :return: 候选列表，每个元素包含 document, bm25_score, bm25_rank
        """
        if not self.bm25 or not self.documents:
            logger.warning("BM25 索引为空，无法检索")
            return []

        try:
            # 对查询进行分词
            query_tokens = self._tokenize(query)

            if not query_tokens:
                logger.warning(f"查询分词结果为空：{query}")
                return []

            # BM25 检索
            scores = self.bm25.get_scores(query_tokens)

            # 按分数排序（降序）
            doc_scores = [(i, score) for i, score in enumerate(scores)]
            doc_scores.sort(key=lambda x: x[1], reverse=True)

            # 取 Top K
            top_results = doc_scores[:top_k]

            # 构建结果
            candidates = []
            for rank, (doc_idx, score) in enumerate(top_results, start=1):
                # 只返回分数 > 0 的结果（有关键词命中）
                if score <= 0:
                    break

                candidates.append({
                    'document': self.documents[doc_idx],
                    'bm25_score': float(score),
                    'bm25_rank': rank
                })

            logger.info(f"BM25 检索完成：query='{query[:50]}', tokens={query_tokens[:10]}, results={len(candidates)}")
            return candidates

        except Exception as e:
            logger.error(f"BM25 检索失败：{str(e)}", exc_info=True)
            return []


def build_bm25_retriever(documents: List[Document]) -> BM25Retriever:
    """
    构建 BM25 检索器（工厂函数）
    :param documents: 文档列表
    :return: BM25Retriever 实例
    """
    return BM25Retriever(documents)


if __name__ == '__main__':
    # 测试
    test_docs = [
        Document(page_content="KB-R2 扫地机器人支持 220V 电压，适合家庭使用", metadata={"id": 1}),
        Document(page_content="产品出现 E03 故障码时，请检查尘盒是否安装到位", metadata={"id": 2}),
        Document(page_content="智能家居产品包括扫地机器人、空气净化器等", metadata={"id": 3}),
    ]

    retriever = BM25Retriever(test_docs)

    # 测试查询
    results = retriever.search("KB-R2 故障码", top_k=3)
    for r in results:
        print(f"Rank {r['bm25_rank']}, Score {r['bm25_score']:.4f}: {r['document'].page_content[:50]}")
