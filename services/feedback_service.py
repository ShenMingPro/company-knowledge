"""
反馈服务：用户对问答结果的反馈
"""
from models.database import get_session, Feedback, ChatMessage, AuditLog
from utils.logger_handler import logger


class FeedbackService:
    """反馈服务"""

    ALLOWED_FEEDBACK_TYPES = ['thumbs_up', 'thumbs_down']

    @staticmethod
    def upsert_feedback(user_id: int, message_id: int, feedback_type: str, content: str = None) -> dict:
        """
        创建或更新用户反馈
        :param user_id: 用户ID
        :param message_id: 消息ID
        :param feedback_type: 反馈类型（thumbs_up/thumbs_down）
        :param content: 反馈内容（可选，点踩时可填写原因）
        :return: {success, feedback_id, error}
        """
        # 验证反馈类型
        if feedback_type not in FeedbackService.ALLOWED_FEEDBACK_TYPES:
            return {
                'success': False,
                'error': f'无效的反馈类型：{feedback_type}'
            }

        # 验证内容长度
        if content and len(content) > 500:
            return {
                'success': False,
                'error': '反馈内容不能超过500字符'
            }

        session = get_session()
        try:
            # 1. 验证消息存在且属于该用户
            message = session.query(ChatMessage).filter_by(id=message_id).first()
            if not message:
                return {
                    'success': False,
                    'error': '消息不存在'
                }

            # 验证消息归属（通过session）
            from models.database import ChatSession
            chat_session = session.query(ChatSession).filter_by(id=message.session_id).first()
            if not chat_session or chat_session.user_id != user_id:
                logger.warning(f"用户{user_id}尝试反馈不属于自己的消息{message_id}")
                return {
                    'success': False,
                    'error': '无权反馈此消息'
                }

            # 2. 查找已有反馈
            existing_feedback = session.query(Feedback).filter_by(
                message_id=message_id,
                user_id=user_id
            ).first()

            if existing_feedback:
                # 更新已有反馈
                existing_feedback.feedback_type = feedback_type
                existing_feedback.feedback_content = content
                feedback_id = existing_feedback.id
                action = '更新'
            else:
                # 创建新反馈
                feedback = Feedback(
                    message_id=message_id,
                    user_id=user_id,
                    feedback_type=feedback_type,
                    feedback_content=content
                )
                session.add(feedback)
                session.flush()
                feedback_id = feedback.id
                action = '创建'

            session.commit()

            logger.info(f"反馈{action}成功：feedback_id={feedback_id}, user={user_id}, message={message_id}, type={feedback_type}")

            # 3. 记录反馈审计
            try:
                audit_log = AuditLog(
                    user_id=user_id,
                    action_type='feedback_submit',
                    action_target=f'feedback:{feedback_id}/message:{message_id}',
                    retrieved_docs={
                        'schema_version': 1,
                        'feedback_type': feedback_type,
                        'has_content': bool(content)
                    },
                    is_success=True
                )
                session.add(audit_log)
                session.commit()
            except Exception as audit_error:
                # 审计失败不影响反馈操作
                logger.error(f"记录反馈审计失败：{str(audit_error)}")

            return {
                'success': True,
                'feedback_id': feedback_id,
                'action': action
            }

        except Exception as e:
            session.rollback()
            logger.error(f"提交反馈失败：{str(e)}", exc_info=True)
            return {
                'success': False,
                'error': '提交反馈失败'
            }
        finally:
            session.close()

    @staticmethod
    def get_message_feedback(user_id: int, message_id: int) -> dict:
        """
        获取用户对某消息的反馈
        :param user_id: 用户ID
        :param message_id: 消息ID
        :return: {success, feedback, error}
        """
        session = get_session()
        try:
            feedback = session.query(Feedback).filter_by(
                message_id=message_id,
                user_id=user_id
            ).first()

            if not feedback:
                return {
                    'success': True,
                    'feedback': None
                }

            return {
                'success': True,
                'feedback': {
                    'id': feedback.id,
                    'feedback_type': feedback.feedback_type,
                    'feedback_content': feedback.feedback_content,
                    'created_at': feedback.created_at
                }
            }

        finally:
            session.close()
