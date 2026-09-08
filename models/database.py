"""
数据库模型定义
"""
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()


class User(Base):
    """用户表"""
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    role = Column(String(20), nullable=False)  # employee, service, admin
    is_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # 关系
    chat_sessions = relationship("ChatSession", back_populates="user")
    feedbacks = relationship("Feedback", back_populates="user")
    audit_logs = relationship("AuditLog", back_populates="user")


class KnowledgeSpace(Base):
    """知识空间表"""
    __tablename__ = 'knowledge_spaces'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(Text)
    allowed_roles = Column(String(200), nullable=False)  # 逗号分隔的角色列表
    is_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # 关系
    documents = relationship("Document", back_populates="knowledge_space")


class Document(Base):
    """文档表（逻辑文档）"""
    __tablename__ = 'documents'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    space_id = Column(Integer, ForeignKey('knowledge_spaces.id'), nullable=False)
    current_version_id = Column(Integer, ForeignKey('document_versions.id'), nullable=True)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)

    # 关系
    knowledge_space = relationship("KnowledgeSpace", back_populates="documents")
    versions = relationship("DocumentVersion", foreign_keys="DocumentVersion.document_id", back_populates="document")
    creator = relationship("User", foreign_keys=[created_by])


class DocumentVersion(Base):
    """文档版本表"""
    __tablename__ = 'document_versions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey('documents.id'), nullable=False)
    version_number = Column(Integer, nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)  # SHA-256
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False)  # pending, processing, success, failed, inactive, deleted
    chunk_count = Column(Integer, default=0)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    indexed_at = Column(DateTime)

    # 关系
    document = relationship("Document", foreign_keys=[document_id], back_populates="versions")


class ChatSession(Base):
    """聊天会话表"""
    __tablename__ = 'chat_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    title = Column(String(200))
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    # 关系
    user = relationship("User", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session")


class ChatMessage(Base):
    """聊天消息表"""
    __tablename__ = 'chat_messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey('chat_sessions.id'), nullable=False)
    user_query = Column(Text, nullable=False)
    system_answer = Column(Text, nullable=False)
    references = Column(JSON)  # 引用信息
    response_time = Column(Integer)  # 响应耗时（毫秒）
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # 关系
    session = relationship("ChatSession", back_populates="messages")
    feedbacks = relationship("Feedback", back_populates="message")


class Feedback(Base):
    """用户反馈表"""
    __tablename__ = 'feedbacks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(Integer, ForeignKey('chat_messages.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    feedback_type = Column(String(20), nullable=False)  # thumbs_up, thumbs_down
    feedback_content = Column(Text)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # 关系
    message = relationship("ChatMessage", back_populates="feedbacks")
    user = relationship("User", back_populates="feedbacks")


class AuditLog(Base):
    """审计日志表"""
    __tablename__ = 'audit_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    action_type = Column(String(50), nullable=False)  # query, upload, update, delete, etc.
    action_target = Column(String(200))  # 操作对象
    query_summary = Column(Text)  # 查询摘要
    retrieved_docs = Column(JSON)  # 召回文档列表
    model_name = Column(String(100))
    response_time = Column(Integer)  # 响应耗时（毫秒）
    is_success = Column(Boolean, nullable=False)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # 关系
    user = relationship("User", back_populates="audit_logs")


# 数据库引擎和会话
def get_engine():
    """获取数据库引擎"""
    db_path = os.getenv("DATABASE_PATH", "data/knowbase.db")
    # 确保目录存在
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return create_engine(f'sqlite:///{db_path}', echo=False)


def get_session():
    """获取数据库会话"""
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()


def init_database():
    """初始化数据库（创建所有表）"""
    engine = get_engine()
    Base.metadata.create_all(engine)
    print("数据库表创建成功")
