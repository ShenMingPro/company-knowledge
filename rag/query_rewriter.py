"""
Query 改写模块

将口语化、指代不明确的问题改写成适合向量检索和 BM25 检索的完整问题。

核心功能：
1. 判断是否需要改写（基于历史和指代词）
2. 使用对话历史补全指代和省略
3. 验证改写结果
4. 失败时安全回退到原始问题

设计原则：
- 简单、稳定、可解释
- 只使用最近 3 轮对话
- 改写后的问题只用于检索，原始问题用于答案生成
- 失败时不影响主流程
"""

import re
import time
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RewriteResult:
    """Query 改写结果"""
    original_query: str  # 用户原始问题
    rewritten_query: str  # 改写后的检索问题
    applied: bool  # 是否应用了改写
    fallback_reason: Optional[str]  # 回退原因
    rewrite_time_ms: int  # 改写耗时（毫秒）


class QueryRewriter:
    """Query 改写器"""

    # 常见指代词和省略表达
    REFERENCE_PATTERNS = [
        r'\b它\b', r'\b这个\b', r'\b那个\b', r'\b上面\b',
        r'\b刚才\b', r'\b前面\b', r'\b另外呢\b', r'\b那.*呢\b',
        r'\b还有吗\b', r'\b这.*怎么\b', r'\b那.*呢\b',
        r'^呢$', r'^还有.*$', r'^另外.*$'
    ]

    # 改写 Prompt 模板
    REWRITE_PROMPT = """你是一个查询改写助手。请根据对话历史，将用户的当前问题改写成一个完整、清晰、适合知识库检索的问题。

对话历史：
{history}

当前问题：{current_query}

要求：
1. 只输出一条完整的检索问题，不要添加编号、解释或其他内容
2. 利用对话历史补全指代词（它、这个、那个等）和省略的主语
3. 不要添加历史中不存在的事实
4. 不要回答问题，只改写问题
5. 如果当前问题已经完整，原样返回或轻微规范化
6. 如果无法确定指代对象，返回原始问题
7. 不要输出 Markdown、代码块或多行内容

改写后的问题："""

    def __init__(
        self,
        chat_model,
        enabled: bool = True,
        history_turns: int = 3,
        max_output_length: int = 200,
        assistant_answer_max_length: int = 500
    ):
        """
        初始化 Query 改写器

        Args:
            chat_model: 现有聊天模型
            enabled: 是否启用改写
            history_turns: 使用最近多少轮对话
            max_output_length: 改写结果最大长度
            assistant_answer_max_length: 助手回答最大长度
        """
        self.chat_model = chat_model
        self.enabled = enabled
        self.history_turns = history_turns
        self.max_output_length = max_output_length
        self.assistant_answer_max_length = assistant_answer_max_length

        logger.info(
            f"Query 改写器初始化: enabled={enabled}, "
            f"history_turns={history_turns}, max_output={max_output_length}"
        )

    def rewrite(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None
    ) -> RewriteResult:
        """
        改写用户查询

        Args:
            query: 用户原始问题
            history: 对话历史，格式：[{"role": "user", "content": "..."}, ...]

        Returns:
            RewriteResult: 改写结果
        """
        start_time = time.time()

        # 1. 检查是否启用
        if not self.enabled:
            logger.info("Query 改写未启用")
            return RewriteResult(
                original_query=query,
                rewritten_query=query,
                applied=False,
                fallback_reason="disabled",
                rewrite_time_ms=0
            )

        # 2. 检查是否有历史
        if not history or len(history) == 0:
            logger.info("无对话历史，跳过改写")
            return RewriteResult(
                original_query=query,
                rewritten_query=query,
                applied=False,
                fallback_reason="no_history",
                rewrite_time_ms=int((time.time() - start_time) * 1000)
            )

        # 3. 判断是否需要改写
        if not self._should_rewrite(query):
            logger.info(f"问题已完整，不需要改写: {query[:50]}")
            return RewriteResult(
                original_query=query,
                rewritten_query=query,
                applied=False,
                fallback_reason="complete_query",
                rewrite_time_ms=int((time.time() - start_time) * 1000)
            )

        # 4. 执行改写
        try:
            # 构建历史上下文
            history_text = self._build_history_context(history)

            # 调用模型
            prompt = self.REWRITE_PROMPT.format(
                history=history_text,
                current_query=query
            )

            rewritten = self.chat_model.invoke(prompt).content

            # 验证和清理结果
            rewritten = self._validate_and_clean(rewritten, query)

            rewrite_time = int((time.time() - start_time) * 1000)

            if rewritten == query:
                logger.info(f"改写后与原问题相同: {query[:50]}")
                return RewriteResult(
                    original_query=query,
                    rewritten_query=query,
                    applied=False,
                    fallback_reason="no_change",
                    rewrite_time_ms=rewrite_time
                )

            logger.info(
                f"Query 改写成功: '{query[:50]}' -> '{rewritten[:50]}' "
                f"({rewrite_time}ms)"
            )

            return RewriteResult(
                original_query=query,
                rewritten_query=rewritten,
                applied=True,
                fallback_reason=None,
                rewrite_time_ms=rewrite_time
            )

        except Exception as e:
            rewrite_time = int((time.time() - start_time) * 1000)
            logger.error(f"Query 改写失败，回退到原始问题: {e}")
            return RewriteResult(
                original_query=query,
                rewritten_query=query,
                applied=False,
                fallback_reason=f"error: {str(e)[:100]}",
                rewrite_time_ms=rewrite_time
            )

    def _should_rewrite(self, query: str) -> bool:
        """
        判断是否需要改写

        策略：
        1. 问题较短（< 10 字符）：需要改写
        2. 包含指代词或省略表达：需要改写
        3. 否则：不需要改写
        """
        # 非常短的问题可能是省略表达
        if len(query.strip()) < 10:
            return True

        # 检查是否包含指代词（使用正则表达式搜索）
        for pattern in self.REFERENCE_PATTERNS:
            if re.search(pattern, query):
                logger.debug(f"检测到指代词: {pattern}")
                return True

        # 如果问题较长（>= 15 字符）且没有指代词，认为已完整
        if len(query.strip()) >= 15:
            return False

        # 其他情况（10-15 字符之间）：谨慎起见，进行改写
        return True

    def _build_history_context(self, history: List[Dict[str, Any]]) -> str:
        """
        构建历史上下文

        只使用最近 N 轮对话，优先使用用户问题，可附带最近一条助手回答。
        """
        # 只取最近 N 轮
        recent_history = history[-self.history_turns * 2:] if len(history) > self.history_turns * 2 else history

        context_lines = []

        for msg in recent_history:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if not content:
                continue

            if role == "user":
                context_lines.append(f"历史问题：{content}")
            elif role == "assistant":
                # 助手回答只保留前 N 个字符
                truncated = content[:self.assistant_answer_max_length]
                if len(content) > self.assistant_answer_max_length:
                    truncated += "..."
                context_lines.append(f"历史回答：{truncated}")

        return "\n".join(context_lines) if context_lines else "（无历史）"

    def _validate_and_clean(self, rewritten: str, original: str) -> str:
        """
        验证和清理改写结果

        失败情况下返回原始问题。
        """
        if not rewritten:
            logger.warning("改写结果为空，使用原始问题")
            return original

        # 去除首尾空白
        rewritten = rewritten.strip()

        # 去除代码块标记
        rewritten = re.sub(r'^```.*\n', '', rewritten)
        rewritten = re.sub(r'\n```$', '', rewritten)
        rewritten = rewritten.strip('`')

        # 去除常见前缀
        rewritten = re.sub(r'^(改写后的问题：|问题：|检索问题：)', '', rewritten)
        rewritten = rewritten.strip()

        # 只取第一行（防止多行输出）
        if '\n' in rewritten:
            first_line = rewritten.split('\n')[0].strip()
            if first_line:
                rewritten = first_line
            else:
                logger.warning("改写结果多行且首行为空，使用原始问题")
                return original

        # 检查长度
        if len(rewritten) > self.max_output_length:
            logger.warning(f"改写结果过长 ({len(rewritten)} > {self.max_output_length})，使用原始问题")
            return original

        # 检查是否为空
        if not rewritten or len(rewritten.strip()) == 0:
            logger.warning("清理后改写结果为空，使用原始问题")
            return original

        # 检查是否包含敏感内容（简单规则）
        suspicious_keywords = ['提示词', '系统提示', 'prompt', 'system', '密钥', 'key', 'token', 'api']
        if any(keyword in rewritten.lower() for keyword in suspicious_keywords):
            logger.warning("改写结果包含可疑内容，使用原始问题")
            return original

        return rewritten


def create_query_rewriter(chat_model, config: Optional[Dict[str, Any]] = None) -> QueryRewriter:
    """
    创建 Query 改写器（工厂方法）

    Args:
        chat_model: 聊天模型
        config: 配置字典

    Returns:
        QueryRewriter 实例
    """
    if config is None:
        config = {}

    return QueryRewriter(
        chat_model=chat_model,
        enabled=config.get('enabled', True),
        history_turns=config.get('history_turns', 3),
        max_output_length=config.get('max_output_length', 200),
        assistant_answer_max_length=config.get('assistant_answer_max_length', 500)
    )
