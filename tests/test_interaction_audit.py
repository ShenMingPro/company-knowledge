"""
问答持久化、审计和反馈测试
"""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models.database import Base, User, KnowledgeSpace, ChatSession, ChatMessage, Feedback, AuditLog
from services.interaction_service import InteractionService
from services.audit_service import AuditService
from services.feedback_service import FeedbackService

# 创建全局测试引擎（所有测试共享同一个内存数据库）
test_engine = create_engine('sqlite:///:memory:', echo=False)
TestSession = sessionmaker(bind=test_engine)


@pytest.fixture(scope='function', autouse=True)
def setup_test_db():
    """设置测试数据库"""
    # 创建所有表
    Base.metadata.create_all(test_engine)

    # Patch get_engine to return our test engine for all database operations
    patcher = patch('models.database.get_engine', return_value=test_engine)
    patcher.start()

    session = TestSession()
    try:
        # 创建测试用户
        user1 = User(id=1, username='testuser1', password_hash='hash1', role='employee', is_enabled=True)
        user2 = User(id=2, username='testuser2', password_hash='hash2', role='employee', is_enabled=True)
        admin = User(id=3, username='admin', password_hash='hash3', role='admin', is_enabled=True)
        session.add_all([user1, user2, admin])

        # 创建知识空间
        space = KnowledgeSpace(
            id=1,
            name='Test Space',
            code='test',
            description='Test description',
            allowed_roles='employee,service,admin'
        )
        session.add(space)

        session.commit()
    finally:
        session.close()

    yield

    # 停止patch
    patcher.stop()

    # 清理所有表
    Base.metadata.drop_all(test_engine)

    # 清理（内存数据库自动清理）


class TestInteractionService:
    """测试问答持久化"""

    def test_record_exchange_creates_session(self, setup_test_db):
        """测试创建新会话"""
        rag_result = {
            'answer': '这是回答',
            'has_answer': True,
            'references': [],
            'total_time_ms': 1000
        }

        result = InteractionService.record_exchange(
            user_id=1,
            session_id=None,
            query='测试问题',
            rag_result=rag_result
        )

        assert result['success'] is True
        assert result['session_id'] is not None
        assert result['message_id'] is not None

        # 验证数据库
        session = TestSession()
        try:
            chat_session = session.query(ChatSession).filter_by(id=result['session_id']).first()
            assert chat_session is not None
            assert chat_session.user_id == 1

            message = session.query(ChatMessage).filter_by(id=result['message_id']).first()
            assert message is not None
            assert message.user_query == '测试问题'
            assert message.system_answer == '这是回答'
            assert message.response_time == 1000
        finally:
            session.close()

        print("✅ 测试创建新会话成功")

    def test_record_exchange_reuses_session(self, setup_test_db):
        """测试复用已有会话"""
        # 第一次创建
        result1 = InteractionService.record_exchange(
            user_id=1,
            session_id=None,
            query='问题1',
            rag_result={'answer': '回答1', 'total_time_ms': 100}
        )

        # 第二次复用
        result2 = InteractionService.record_exchange(
            user_id=1,
            session_id=result1['session_id'],
            query='问题2',
            rag_result={'answer': '回答2', 'total_time_ms': 200}
        )

        assert result1['session_id'] == result2['session_id']
        assert result1['message_id'] != result2['message_id']

        # 验证两条消息都在同一会话
        session = TestSession()
        try:
            messages = session.query(ChatMessage).filter_by(session_id=result1['session_id']).all()
            assert len(messages) == 2
        finally:
            session.close()

        print("✅ 测试复用会话成功")

    def test_record_refusal(self, setup_test_db):
        """测试记录拒答"""
        rag_result = {
            'answer': '当前知识库中没有找到足够依据...',
            'has_answer': False,
            'refusal_reason': 'below_relevance_threshold',
            'references': [],
            'total_time_ms': 500
        }

        result = InteractionService.record_exchange(
            user_id=1,
            session_id=None,
            query='不相关的问题',
            rag_result=rag_result
        )

        assert result['success'] is True

        session = TestSession()
        try:
            message = session.query(ChatMessage).filter_by(id=result['message_id']).first()
            assert message.system_answer.startswith('当前知识库中没有找到')
        finally:
            session.close()

        print("✅ 测试记录拒答成功")


class TestAuditService:
    """测试审计服务"""

    def test_record_rag_query_success(self, setup_test_db):
        """测试记录成功的RAG查询"""
        rag_result = {
            'answer': '回答内容',
            'has_answer': True,
            'refusal_reason': None,
            'references': [
                {
                    'document_id': 1,
                    'document_version_id': 1,
                    'source_name': 'test.txt',
                    'page': None,
                    'section': '分片1',
                    'chunk_index': 0,
                    'relevance_score': 0.85,
                    'content': '这是内容'  # 不应保存
                }
            ],
            'retrieved_count': 3,
            'filtered_count': 2,
            'valid_count': 2,
            'retrieval_time_ms': 100,
            'generation_time_ms': 500,
            'total_time_ms': 600,
            'model_name': 'qwen-plus'
        }

        result = AuditService.record_rag_query(
            user_id=1,
            message_id=1,
            query='测试问题',
            rag_result=rag_result
        )

        assert result['success'] is True

        # 验证审计记录
        session = TestSession()
        try:
            audit = session.query(AuditLog).filter_by(id=result['audit_log_id']).first()
            assert audit is not None
            assert audit.action_type == 'rag_query'
            assert audit.action_target == 'chat_message:1'
            assert audit.is_success is True
            assert audit.query_summary == '测试问题'
            assert audit.model_name == 'qwen-plus'
            assert audit.response_time == 600

            # 验证retrieved_docs结构
            docs = audit.retrieved_docs
            assert docs['schema_version'] == 1
            assert docs['has_answer'] is True
            assert docs['refusal_reason'] is None
            assert docs['metrics']['retrieved_count'] == 3
            assert docs['metrics']['valid_count'] == 2

            # 验证引用不包含content
            assert len(docs['references']) == 1
            assert 'content' not in docs['references'][0]
            assert docs['references'][0]['relevance_score'] == 0.85
        finally:
            session.close()

        print("✅ 测试记录成功RAG查询")

    def test_record_rag_query_refusal(self, setup_test_db):
        """测试记录拒答（is_success=True）"""
        rag_result = {
            'answer': '拒答内容',
            'has_answer': False,
            'refusal_reason': 'below_relevance_threshold',
            'references': [],
            'retrieved_count': 3,
            'filtered_count': 0,
            'valid_count': 0,
            'retrieval_time_ms': 100,
            'generation_time_ms': 0,
            'total_time_ms': 100,
            'model_name': 'qwen-plus'
        }

        result = AuditService.record_rag_query(
            user_id=1,
            message_id=2,
            query='无关问题',
            rag_result=rag_result
        )

        session = TestSession()
        try:
            audit = session.query(AuditLog).filter_by(id=result['audit_log_id']).first()
            # 业务拒答仍然是技术成功
            assert audit.is_success is True
            assert audit.retrieved_docs['has_answer'] is False
            assert audit.retrieved_docs['refusal_reason'] == 'below_relevance_threshold'
            assert audit.error_message is None
        finally:
            session.close()

        print("✅ 测试记录拒答（is_success=True）")

    def test_record_rag_query_error(self, setup_test_db):
        """测试记录技术错误（is_success=False）"""
        rag_result = {
            'answer': '拒答内容',
            'has_answer': False,
            'refusal_reason': 'generation_error',
            'references': [],
            'retrieved_count': 0,
            'total_time_ms': 50,
            'error_internal': 'Traceback (most recent call last):\n  File "test.py", line 10\n    Exception: 模型调用失败'
        }

        result = AuditService.record_rag_query(
            user_id=1,
            message_id=3,
            query='错误问题',
            rag_result=rag_result
        )

        session = TestSession()
        try:
            audit = session.query(AuditLog).filter_by(id=result['audit_log_id']).first()
            # 技术异常是失败
            assert audit.is_success is False
            assert audit.error_message is not None
            # 验证已清洗（只保留第一行）
            assert audit.error_message == 'Traceback (most recent call last):'
            assert '\n' not in audit.error_message  # 不应包含多行
        finally:
            session.close()

        print("✅ 测试记录技术错误（is_success=False）")

    def test_sanitize_sensitive_info(self, setup_test_db):
        """测试脱敏敏感信息"""
        # 测试 API_KEY 脱敏
        sanitized = AuditService.sanitize_text('My API_KEY=abc123456789')
        assert 'abc123456789' not in sanitized
        assert '[API_KEY]' in sanitized

        # 测试 password 脱敏
        sanitized = AuditService.sanitize_text('password=secret123')
        assert 'secret123' not in sanitized
        assert '[PASSWORD]' in sanitized

        # 测试 secret 脱敏
        sanitized = AuditService.sanitize_text('secret=mysecret456')
        assert 'mysecret456' not in sanitized
        assert '[SECRET]' in sanitized

        # 测试 bearer token 脱敏
        sanitized = AuditService.sanitize_text('Authorization: bearer token-xyz-123')
        assert 'token-xyz-123' not in sanitized
        assert '[BEARER_TOKEN]' in sanitized

        # 测试正常文本不脱敏
        normal_text = 'Normal question about data'
        sanitized = AuditService.sanitize_text(normal_text)
        assert sanitized == normal_text

        print("✅ 测试敏感信息脱敏")

    def test_list_logs_admin_only(self, setup_test_db):
        """测试只有管理员可以查询审计"""
        # 创建一条审计记录
        AuditService.record_rag_query(
            user_id=1,
            message_id=1,
            query='test',
            rag_result={'answer': 'test', 'total_time_ms': 100}
        )

        # 管理员可以查询
        result_admin = AuditService.list_logs(user_role='admin')
        assert result_admin['success'] is True
        assert len(result_admin['logs']) > 0

        # 员工不能查询
        result_employee = AuditService.list_logs(user_role='employee')
        assert result_employee['success'] is False
        assert result_employee['error'] == '权限不足'

        print("✅ 测试审计查询权限控制")

    def test_record_document_operation(self, setup_test_db):
        """测试记录文档操作"""
        result = AuditService.record_document_operation(
            user_id=3,
            action_type='document_upload',
            document_id=1,
            version_id=1,
            space_id=1,
            is_success=True
        )

        assert result['success'] is True

        session = TestSession()
        try:
            audit = session.query(AuditLog).filter_by(id=result['audit_log_id']).first()
            assert audit.action_type == 'document_upload'
            assert audit.action_target == 'document:1/version:1'
            assert audit.is_success is True
        finally:
            session.close()

        print("✅ 测试记录文档操作")


class TestFeedbackService:
    """测试反馈服务"""

    def test_submit_feedback_success(self, setup_test_db):
        """测试提交反馈"""
        # 先创建会话和消息
        session = TestSession()
        try:
            chat_session = ChatSession(user_id=1)
            session.add(chat_session)
            session.flush()

            message = ChatMessage(
                session_id=chat_session.id,
                user_query='问题',
                system_answer='回答',
                response_time=100
            )
            session.add(message)
            session.commit()
            message_id = message.id
        finally:
            session.close()

        # 提交点赞
        result = FeedbackService.upsert_feedback(
            user_id=1,
            message_id=message_id,
            feedback_type='thumbs_up'
        )

        assert result['success'] is True
        assert result['action'] == '创建'

        print("✅ 测试提交反馈成功")

    def test_update_feedback(self, setup_test_db):
        """测试更新反馈（从点赞改为点踩）"""
        # 创建消息
        session = TestSession()
        try:
            chat_session = ChatSession(user_id=1)
            session.add(chat_session)
            session.flush()

            message = ChatMessage(
                session_id=chat_session.id,
                user_query='问题',
                system_answer='回答',
                response_time=100
            )
            session.add(message)
            session.commit()
            message_id = message.id
        finally:
            session.close()

        # 第一次点赞
        result1 = FeedbackService.upsert_feedback(
            user_id=1,
            message_id=message_id,
            feedback_type='thumbs_up'
        )

        # 第二次改为点踩
        result2 = FeedbackService.upsert_feedback(
            user_id=1,
            message_id=message_id,
            feedback_type='thumbs_down',
            content='不够准确'
        )

        assert result2['success'] is True
        assert result2['action'] == '更新'
        assert result1['feedback_id'] == result2['feedback_id']

        # 验证只有一条记录
        session = TestSession()
        try:
            feedbacks = session.query(Feedback).filter_by(message_id=message_id, user_id=1).all()
            assert len(feedbacks) == 1
            assert feedbacks[0].feedback_type == 'thumbs_down'
            assert feedbacks[0].feedback_content == '不够准确'
        finally:
            session.close()

        print("✅ 测试更新反馈成功")

    def test_feedback_permission(self, setup_test_db):
        """测试用户不能反馈其他用户的消息"""
        # 用户1创建消息
        session = TestSession()
        try:
            chat_session = ChatSession(user_id=1)
            session.add(chat_session)
            session.flush()

            message = ChatMessage(
                session_id=chat_session.id,
                user_query='问题',
                system_answer='回答',
                response_time=100
            )
            session.add(message)
            session.commit()
            message_id = message.id
        finally:
            session.close()

        # 用户2尝试反馈
        result = FeedbackService.upsert_feedback(
            user_id=2,
            message_id=message_id,
            feedback_type='thumbs_up'
        )

        assert result['success'] is False
        assert result['error'] == '无权反馈此消息'

        print("✅ 测试反馈权限控制")

    def test_feedback_content_length(self, setup_test_db):
        """测试反馈内容长度限制"""
        # 创建消息
        session = TestSession()
        try:
            chat_session = ChatSession(user_id=1)
            session.add(chat_session)
            session.flush()

            message = ChatMessage(
                session_id=chat_session.id,
                user_query='问题',
                system_answer='回答',
                response_time=100
            )
            session.add(message)
            session.commit()
            message_id = message.id
        finally:
            session.close()

        # 超长内容
        long_content = 'x' * 501

        result = FeedbackService.upsert_feedback(
            user_id=1,
            message_id=message_id,
            feedback_type='thumbs_down',
            content=long_content
        )

        assert result['success'] is False
        assert '500' in result['error']

        print("✅ 测试反馈内容长度限制")


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("问答持久化、审计和反馈测试")
    print("=" * 60)

    pytest.main([__file__, '-v', '-s'])


if __name__ == '__main__':
    run_all_tests()
