import os
from abc import ABC, abstractmethod
from typing import Optional
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
    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        provider = os.getenv("MODEL_PROVIDER", "dashscope")

        if provider == "dashscope":
            api_key = os.getenv("DASHSCOPE_API_KEY")
            if not api_key:
                raise ValueError("未配置DASHSCOPE_API_KEY环境变量")
            return ChatTongyi(
                model=rag_conf["chat_model_name"],
                dashscope_api_key=api_key
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


chat_model = ChatModelFactory().generator()
embed_model = EmbeddingsFactory().generator()
