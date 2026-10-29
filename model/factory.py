import os
from abc import ABC, abstractmethod
from typing import Optional, Literal
from langchain_core.embeddings import Embeddings
from langchain_community.chat_models.tongyi import BaseChatModel
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.chat_models.tongyi import ChatTongyi
from utils.config_handler import rag_conf
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


class BaseModelFactory(ABC):
    @abstractmethod
    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        pass


class ChatModelFactory(BaseModelFactory):
    def __init__(self, purpose: Optional[str] = None):
        """
        :param purpose: 模型用途 ("answer" 用于答案生成, "judge" 用于评测裁判)
        """
        self.purpose = purpose

    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        provider = os.getenv("MODEL_PROVIDER", "dashscope")

        if provider == "dashscope":
            api_key = os.getenv("DASHSCOPE_API_KEY")
            if not api_key:
                raise ValueError("未配置DASHSCOPE_API_KEY环境变量")

            # 根据用途读取不同的配置
            if self.purpose == "answer":
                temperature = rag_conf.get("chat_model_temperature", 0.0)
                top_p = rag_conf.get("chat_model_top_p", 1.0)
            elif self.purpose == "judge":
                temperature = rag_conf.get("judge_model_temperature", 0.0)
                top_p = rag_conf.get("judge_model_top_p", 1.0)
            else:
                # 默认使用 chat_model 配置
                temperature = rag_conf.get("chat_model_temperature", 0.0)
                top_p = rag_conf.get("chat_model_top_p", 1.0)

            # ChatTongyi 通过 model_kwargs 传递参数
            return ChatTongyi(
                model=rag_conf["chat_model_name"],
                dashscope_api_key=api_key,
                streaming=self.purpose != "judge",
                model_kwargs={
                    "temperature": temperature,
                    "top_p": top_p
                }
            )
        elif provider == "ollama":
            # 未来支持Ollama
            raise NotImplementedError("Ollama支持将在后续版本实现")
        else:
            raise ValueError(f"不支持的模型提供商: {provider}")


class EmbeddingsFactory(BaseModelFactory):
    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        provider = os.getenv("MODEL_PROVIDER", "dashscope")

        if provider == "dashscope":
            api_key = os.getenv("DASHSCOPE_API_KEY")
            if not api_key:
                raise ValueError("未配置DASHSCOPE_API_KEY环境变量")
            return DashScopeEmbeddings(
                model=rag_conf["embedding_model_name"],
                dashscope_api_key=api_key
            )
        elif provider == "ollama":
            # 未来支持Ollama
            raise NotImplementedError("Ollama支持将在后续版本实现")
        else:
            raise ValueError(f"不支持的模型提供商: {provider}")


def create_chat_model(purpose: Literal["answer", "judge"] = "answer") -> BaseChatModel:
    """
    创建聊天模型
    :param purpose: 模型用途 ("answer" 用于答案生成, "judge" 用于评测裁判)
    :return: 聊天模型实例
    """
    return ChatModelFactory(purpose=purpose).generator()


# 兼容旧代码：保留 chat_model 别名（用于答案生成）
chat_model = ChatModelFactory(purpose="answer").generator()
embed_model = EmbeddingsFactory().generator()
