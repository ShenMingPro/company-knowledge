"""
Reranker 精排模块：使用 DashScope gte-rerank-v2 对候选文档重新排序
"""
import os
import time
from typing import List, Optional, Dict
from dataclasses import dataclass
from utils.logger_handler import logger


@dataclass
class RerankOutcome:
    """Rerank 结果"""
    candidates: List  # 重排后的候选列表（RetrievalCandidate）
    applied: bool  # 是否实际执行精排
    model: Optional[str]  # 模型名
    input_count: int  # 输入候选数
    output_count: int  # 输出候选数
    rerank_time_ms: float  # 精排耗时（毫秒）
    fallback_reason: Optional[str]  # 降级或跳过原因


class Reranker:
    """DashScope Reranker 封装"""

    def __init__(self, config: Dict):
        """
        初始化 Reranker
        :param config: 配置字典
        """
        self.enabled = config.get('enabled', True)
        self.model = config.get('model', 'gte-rerank-v2')
        self.candidate_k = config.get('candidate_k', 10)
        self.output_top_k = config.get('output_top_k', 3)
        self.max_query_chars = config.get('max_query_chars', 500)
        self.max_document_chars = config.get('max_document_chars', 1200)
        self.timeout_seconds = config.get('timeout_seconds', 8)

        # 校验配置
        if not self._validate_config():
            self.enabled = False
            logger.warning("Reranker 配置无效，已禁用")

        # 检查 API Key
        self.api_key = os.getenv('DASHSCOPE_API_KEY')
        if not self.api_key and self.enabled:
            logger.warning("缺少 DASHSCOPE_API_KEY 环境变量，Reranker 将降级")

    def _validate_config(self) -> bool:
        """
        校验配置
        :return: 配置是否有效
        """
        if self.candidate_k < 1 or self.output_top_k < 1:
            logger.error(f"Reranker 配置无效：candidate_k={self.candidate_k}, output_top_k={self.output_top_k}")
            return False

        if self.candidate_k < self.output_top_k:
            logger.error(f"Reranker 配置无效：candidate_k ({self.candidate_k}) < output_top_k ({self.output_top_k})")
            return False

        return True

    def rerank(self, query: str, candidates: List, top_n: int = None) -> RerankOutcome:
        """
        精排候选文档
        :param query: 查询问题（实际用于检索的问题）
        :param candidates: 候选列表（RetrievalCandidate 对象）
        :param top_n: 最终保留数量（默认使用配置）
        :return: RerankOutcome
        """
        start_time = time.time()

        if top_n is None:
            top_n = self.output_top_k

        # 1. 检查是否启用
        if not self.enabled:
            return self._fallback(candidates, "disabled", start_time)

        # 2. 检查 API Key
        if not self.api_key:
            return self._fallback(candidates, "missing_api_key", start_time)

        # 3. 检查候选数量
        if not candidates:
            return self._fallback(candidates, "no_candidates", start_time)

        if len(candidates) <= top_n:
            logger.info(f"候选数 ({len(candidates)}) <= top_n ({top_n})，跳过 Rerank")
            return self._fallback(candidates, "insufficient_candidates", start_time)

        # 4. 校验候选元数据
        for cand in candidates:
            doc = cand.document
            if not doc.metadata.get('document_version_id') or doc.metadata.get('chunk_index') is None:
                logger.warning("候选缺少元数据归属，拒绝发送到云端 Reranker")
                return self._fallback(candidates, "unverifiable_candidate_metadata", start_time)

        # 5. 准备文本
        query_text = self._truncate_text(query, self.max_query_chars)
        documents = [self._truncate_text(c.document.page_content, self.max_document_chars) for c in candidates]

        # 6. 调用 DashScope
        try:
            import dashscope
            from dashscope import TextReRank

            response = TextReRank.call(
                model=self.model,
                query=query_text,
                documents=documents,
                return_documents=False,
                api_key=self.api_key
            )

            rerank_time_ms = (time.time() - start_time) * 1000

            # 7. 检查响应
            if response.status_code != 200:
                logger.error(f"DashScope Rerank 失败：status_code={response.status_code}, message={response.message}")
                return self._fallback(candidates, "api_error", start_time)

            if not response.output or not response.output.results:
                logger.warning("DashScope Rerank 返回空结果")
                return self._fallback(candidates, "invalid_response", start_time)

            # 8. 解析结果
            results = response.output.results
            reranked_candidates = []
            seen_indices = set()

            for rank, result in enumerate(results[:top_n], 1):
                index = result.index
                relevance_score = result.relevance_score

                # 检查索引有效性
                if index < 0 or index >= len(candidates):
                    logger.warning(f"DashScope 返回越界索引：{index}")
                    continue

                if index in seen_indices:
                    logger.warning(f"DashScope 返回重复索引：{index}")
                    continue

                seen_indices.add(index)

                # 获取原始候选并更新精排信息
                cand = candidates[index]
                cand.rerank_rank = rank
                cand.rerank_score = relevance_score
                reranked_candidates.append(cand)

            if not reranked_candidates:
                logger.warning("解析 DashScope 结果后无有效候选")
                return self._fallback(candidates, "invalid_response", start_time)

            logger.info(f"Rerank 成功：model={self.model}, input={len(candidates)}, "
                       f"output={len(reranked_candidates)}, time={rerank_time_ms:.1f}ms")

            return RerankOutcome(
                candidates=reranked_candidates,
                applied=True,
                model=self.model,
                input_count=len(candidates),
                output_count=len(reranked_candidates),
                rerank_time_ms=rerank_time_ms,
                fallback_reason=None
            )

        except ImportError:
            logger.error("缺少 dashscope 库")
            return self._fallback(candidates, "missing_sdk", start_time)

        except Exception as e:
            logger.error(f"Rerank 调用异常：{str(e)}", exc_info=True)
            return self._fallback(candidates, "api_error", start_time)

    def _fallback(self, candidates: List, reason: str, start_time: float) -> RerankOutcome:
        """
        降级：保留原始 RRF 排序
        :param candidates: 原始候选列表
        :param reason: 降级原因
        :param start_time: 开始时间
        :return: RerankOutcome
        """
        elapsed_ms = (time.time() - start_time) * 1000

        # 截取 Top K
        output_candidates = candidates[:self.output_top_k]

        return RerankOutcome(
            candidates=output_candidates,
            applied=False,
            model=None,
            input_count=len(candidates),
            output_count=len(output_candidates),
            rerank_time_ms=elapsed_ms,
            fallback_reason=reason
        )

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        """
        截断文本
        :param text: 原始文本
        :param max_chars: 最大字符数
        :return: 截断后的文本
        """
        if len(text) <= max_chars:
            return text
        return text[:max_chars]


def create_reranker(config: Dict) -> Optional[Reranker]:
    """
    创建 Reranker 实例
    :param config: 配置字典
    :return: Reranker 实例或 None
    """
    if not config.get('enabled', True):
        logger.info("Reranker 配置禁用")
        return None

    return Reranker(config)
