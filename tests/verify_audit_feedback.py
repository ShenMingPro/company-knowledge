"""
审计日志与用户反馈功能验证脚本
模拟用户操作并验证数据库记录
"""
import sys
import os
import io

# 设置 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models.database import get_session, ChatSession, ChatMessage, Feedback, AuditLog
from services.interaction_service import InteractionService
from services.audit_service import AuditService
from services.feedback_service import FeedbackService


def test_interaction_persistence():
    """测试问答持久化"""
    print("\n=== 测试1: 问答持久化 ===")

    # 模拟第一次提问
    result1 = InteractionService.record_exchange(
        user_id=1,
        session_id=None,
        query="产品的主要功能是什么？",
        rag_result={
            'answer': '产品具有以下主要功能...',
            'has_answer': True,
            'references': [],
            'total_time_ms': 1500
        }
    )

    print(f"✓ 创建新会话: session_id={result1['session_id']}, message_id={result1['message_id']}")

    # 模拟同一会话第二次提问
    result2 = InteractionService.record_exchange(
        user_id=1,
        session_id=result1['session_id'],
        query="如何使用这个功能？",
        rag_result={
            'answer': '使用方法如下...',
            'has_answer': True,
            'references': [],
            'total_time_ms': 1200
        }
    )

    print(f"✓ 复用会话: session_id={result2['session_id']}, message_id={result2['message_id']}")

    # 验证数据库
    session = get_session()
    try:
        messages = session.query(ChatMessage).filter_by(session_id=result1['session_id']).all()
        print(f"✓ 验证: 会话中有 {len(messages)} 条消息")

        assert len(messages) == 2, "应该有2条消息"
        assert result1['session_id'] == result2['session_id'], "session_id应该相同"
        print("✅ 问答持久化测试通过")
        return True
    except Exception as e:
        print(f"❌ 问答持久化测试失败: {e}")
        return False
    finally:
        session.close()


def test_feedback_upsert():
    """测试用户反馈upsert模式"""
    print("\n=== 测试2: 用户反馈 ===")

    # 创建一条消息用于反馈
    result = InteractionService.record_exchange(
        user_id=1,
        session_id=None,
        query="测试问题",
        rag_result={'answer': '测试答案', 'total_time_ms': 100}
    )

    message_id = result['message_id']

    # 第一次点赞
    feedback1 = FeedbackService.upsert_feedback(
        user_id=1,
        message_id=message_id,
        feedback_type='thumbs_up'
    )
    print(f"✓ 提交点赞: feedback_id={feedback1['feedback_id']}")

    # 第二次改为点踩
    feedback2 = FeedbackService.upsert_feedback(
        user_id=1,
        message_id=message_id,
        feedback_type='thumbs_down',
        content='回答不够详细'
    )
    print(f"✓ 更新为点踩: feedback_id={feedback2['feedback_id']}")

    # 验证数据库
    session = get_session()
    try:
        feedbacks = session.query(Feedback).filter_by(
            message_id=message_id,
            user_id=1
        ).all()

        print(f"✓ 验证: 该消息有 {len(feedbacks)} 条反馈记录")
        assert len(feedbacks) == 1, "应该只有1条反馈（upsert模式）"
        assert feedbacks[0].feedback_type == 'thumbs_down', "应该是点踩"
        # Feedback 模型没有 content 字段，使用 feedback_content
        assert feedbacks[0].feedback_content == '回答不够详细', "内容应该匹配"
        print("✅ 用户反馈测试通过")
        return True
    except Exception as e:
        print(f"❌ 用户反馈测试失败: {e}")
        return False
    finally:
        session.close()


def test_audit_logging():
    """测试审计日志"""
    print("\n=== 测试3: 审计日志 ===")

    # 记录RAG查询审计（成功）
    result1 = AuditService.record_rag_query(
        user_id=1,
        message_id=1,
        query="产品保修期是多久？",
        rag_result={
            'answer': '保修期为1年',
            'has_answer': True,
            'refusal_reason': None,
            'references': [
                {
                    'document_id': 1,
                    'source_name': 'warranty.pdf',
                    'relevance_score': 0.85,
                    'content': '这是内容，不应该保存到审计'
                }
            ],
            'retrieved_count': 5,
            'valid_count': 3,
            'retrieval_time_ms': 120,
            'generation_time_ms': 800,
            'total_time_ms': 920
        }
    )
    print(f"✓ 记录成功查询: audit_log_id={result1['audit_log_id']}")

    # 记录RAG查询审计（拒答）
    result2 = AuditService.record_rag_query(
        user_id=1,
        message_id=2,
        query="如何修理设备？",
        rag_result={
            'answer': '当前知识库中没有找到足够依据...',
            'has_answer': False,
            'refusal_reason': 'space_permission_denied',
            'references': [],
            'retrieved_count': 0,
            'total_time_ms': 50
        }
    )
    print(f"✓ 记录拒答查询: audit_log_id={result2['audit_log_id']}")

    # 记录文档操作审计
    result3 = AuditService.record_document_operation(
        user_id=3,
        action_type='document_upload',
        document_id=1,
        version_id=1,
        space_id=1,
        is_success=True
    )
    print(f"✓ 记录文档上传: audit_log_id={result3['audit_log_id']}")

    # 验证数据库
    session = get_session()
    try:
        # 验证成功查询
        audit1 = session.query(AuditLog).filter_by(id=result1['audit_log_id']).first()
        assert audit1.action_type == 'rag_query'
        assert audit1.is_success is True
        assert audit1.query_summary == '产品保修期是多久？'

        # 验证审计JSON不包含content
        import json
        # retrieved_docs 在 SQLite 中是 JSON 类型，已经自动反序列化为 dict
        retrieved_docs = audit1.retrieved_docs if isinstance(audit1.retrieved_docs, dict) else (json.loads(audit1.retrieved_docs) if audit1.retrieved_docs else {})
        if retrieved_docs.get('references'):
            for ref in retrieved_docs['references']:
                assert 'content' not in ref, "审计JSON不应包含content字段"
        print("✓ 验证: 审计JSON不包含敏感内容")

        # 验证拒答查询
        audit2 = session.query(AuditLog).filter_by(id=result2['audit_log_id']).first()
        assert audit2.is_success is True, "业务拒答应该是is_success=True"
        retrieved_docs2 = audit2.retrieved_docs if isinstance(audit2.retrieved_docs, dict) else (json.loads(audit2.retrieved_docs) if audit2.retrieved_docs else {})
        assert retrieved_docs2.get('has_answer') is False
        assert retrieved_docs2.get('refusal_reason') == 'space_permission_denied'
        print("✓ 验证: 拒答记录为is_success=True")

        # 验证文档操作
        audit3 = session.query(AuditLog).filter_by(id=result3['audit_log_id']).first()
        assert audit3.action_type == 'document_upload'
        assert audit3.is_success is True
        print("✓ 验证: 文档操作审计记录正确")

        print("✅ 审计日志测试通过")
        return True
    except Exception as e:
        print(f"❌ 审计日志测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        session.close()


def test_sanitization():
    """测试敏感信息脱敏"""
    print("\n=== 测试4: 敏感信息脱敏 ===")

    # 测试API密钥脱敏
    text1 = "My API_KEY=sk_test_12345678"
    sanitized1 = AuditService.sanitize_text(text1)
    print(f"原文: {text1}")
    print(f"脱敏后: {sanitized1}")
    assert 'sk_test_12345678' not in sanitized1
    assert '[API_KEY]' in sanitized1
    print("✓ API密钥脱敏成功")

    # 测试密码脱敏
    text2 = "password=secret123"
    sanitized2 = AuditService.sanitize_text(text2)
    print(f"原文: {text2}")
    print(f"脱敏后: {sanitized2}")
    assert 'secret123' not in sanitized2
    assert '[PASSWORD]' in sanitized2
    print("✓ 密码脱敏成功")

    # 测试token脱敏
    text3 = "Authorization: bearer token-xyz-123"
    sanitized3 = AuditService.sanitize_text(text3)
    print(f"原文: {text3}")
    print(f"脱敏后: {sanitized3}")
    assert 'token-xyz-123' not in sanitized3
    assert '[BEARER_TOKEN]' in sanitized3
    print("✓ Token脱敏成功")

    print("✅ 敏感信息脱敏测试通过")
    return True


def test_admin_access():
    """测试管理员权限"""
    print("\n=== 测试5: 审计日志访问权限 ===")

    # 非管理员尝试访问
    result1 = AuditService.list_logs(
        user_role='employee',
        page=1,
        page_size=10
    )
    assert result1['success'] is False
    assert result1['error'] == '权限不足'
    print("✓ 普通用户被拒绝访问")

    # 管理员访问
    result2 = AuditService.list_logs(
        user_role='admin',
        page=1,
        page_size=10
    )
    assert result2['success'] is True
    print(f"✓ 管理员成功访问，查询到 {result2['total']} 条记录")

    print("✅ 权限控制测试通过")
    return True


def main():
    """运行所有验证测试"""
    print("\n" + "="*60)
    print("审计日志与用户反馈功能验证")
    print("="*60)

    results = []

    try:
        results.append(("问答持久化", test_interaction_persistence()))
        results.append(("用户反馈", test_feedback_upsert()))
        results.append(("审计日志", test_audit_logging()))
        results.append(("敏感信息脱敏", test_sanitization()))
        results.append(("权限控制", test_admin_access()))
    except Exception as e:
        print(f"\n❌ 测试过程出错: {e}")
        import traceback
        traceback.print_exc()

    # 输出总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{name}: {status}")

    print(f"\n总计: {passed}/{total} 通过")

    if passed == total:
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败")
        return 1


if __name__ == '__main__':
    sys.exit(main())
