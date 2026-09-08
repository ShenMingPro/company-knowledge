
"""
RAG服务类：支持权限感知的检索和带引用的问答
"""
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from rag.vector_store import VectorStoreService
from utils.prompt_loader import load_rag_prompts
from langchain_core.prompts import PromptTemplate
from model.factory import chat_model
from utils.logger_handler import logger


class RagSummarizeService(object):
    """RAG问答服务"""

    # 相关性阈值（可根据实际情况调整）
    RELEVANCE_THRESHOLD = 0.3

    def __init__(self, user_role: str = None):
        """
        初始化RAG服务
        :param user_role: 用户角色，用于权限过滤
        """
        self.user_role = user_role
        self.vector_store = VectorStoreService()
        self.retriever = self.vector_store.get_retriever(user_role=user_role)
        self.prompt_text = load_rag_prompts()
        self.prompt_template = PromptTemplate.from_template(self.prompt_text)
        self.model = chat_model
        self.chain = self._init_chain()

    def _init_chain(self):
        chain = self.prompt_template | self.model | StrOutputParser()
        return chain

    def retriever_docs(self, query: str, k: int = None) -> list[Document]:
        """检索相关文档"""
        if k:
            retriever = self.vector_store.get_retriever(user_role=self.user_role, k=k)
            return retriever.invoke(query)
        return self.retriever.invoke(query)

    def format_references(self, docs: list[Document]) -> list[dict]:
        """
        格式化引用信息
        :param docs: 文档列表
        :return: 引用信息列表
        """
        references = []
        seen = set()  # 去重

        for doc in docs:
            metadata = doc.metadata
            source_name = metadata.get('source_name', '未知来源')
            page_num = metadata.get('page', metadata.get('page_number'))
            chunk_index = metadata.get('chunk_index', 0)

            # 构建唯一标识
            ref_key = f"{source_name}_{page_num}_{chunk_index}"
            if ref_key in seen:
                continue
            seen.add(ref_key)

            # 构建引用信息
            ref = {
                'source_name': source_name,
                'content': doc.page_content[:200] + '...' if len(doc.page_content) > 200 else doc.page_content,
                'metadata': {
                    'page': page_num,
                    'chunk_index': chunk_index
                }
            }
            references.append(ref)

        return references

    def check_relevance(self, docs: list[Document]) -> bool:
        """
        检查检索结果的相关性
        :param docs: 文档列表
        :return: 是否有足够相关的文档
        """
        # 简单策略：检查是否有文档返回
        # 更复杂的策略可以检查相似度分数
        return len(docs) > 0

    def rag_summarize(self, query: str) -> dict:
        """
        RAG问答（带引用）
        :param query: 用户问题
        :return: 包含答案和引用的字典
        """
        try:
            # 检索相关文档
            context_docs = self.retriever_docs(query)

            # 检查相关性
            if not self.check_relevance(context_docs):
                logger.info(f"问题相关性不足，拒绝回答：{query}")
                return {
                    "answer": "当前知识库中没有找到足够依据，无法确认该问题。建议联系管理员补充相关文档，或转交专业人员处理。",
                    "references": [],
                    "has_answer": False
                }

            # 构建上下文
            context = ""
            counter = 0
            for doc in context_docs:
                counter += 1
                context += f"【参考资料{counter}】: {doc.page_content}\n"

            # 生成答案
            answer = self.chain.invoke({
                "input": query,
                "context": context,
            })

            # 格式化引用
            references = self.format_references(context_docs)

            logger.info(f"RAG问答成功：{query[:50]}...")
            return {
                "answer": answer,
                "references": references,
                "has_answer": True
            }

        except Exception as e:
            logger.error(f"RAG问答失败：{str(e)}", exc_info=True)
            return {
                "answer": f"系统错误：{str(e)}",
                "references": [],
                "has_answer": False,
                "error": str(e)
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
