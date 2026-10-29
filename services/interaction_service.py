"""
问答交互服务：问答记录持久化
"""
from datetime import datetime
from models.database import get_session, ChatSession, ChatMessage
from utils.logger_handler import logger


class InteractionService:
    """问答交互服务"""

    @staticmethod
    def record_exchange(user_id: int, session_id: int, query: str, rag_result: dict) -> dict:
        """
        记录一次问答交互
        :param user_id: 用户ID
        :param session_id: 会话ID（None时创建新会话）
        :param query: 用户问题
        :param rag_result: RAG返回结果
        :return: {session_id, message_id, success, error}
        """
        session = get_session()
        try:
            # 1. 获取或创建会话
            if session_id:
                chat_session = session.query(ChatSession).filter_by(
                    id=session_id,
                    user_id=user_id
                ).first()
                if not chat_session:
                    logger.warning(f"会话{session_id}不存在或不属于用户{user_id}，创建新会话")
                    chat_session = ChatSession(user_id=user_id)
                    session.add(chat_session)
                    session.flush()
            else:
                chat_session = ChatSession(user_id=user_id)
                session.add(chat_session)
                session.flush()

            # 2. 保存消息
            message = ChatMessage(
                session_id=chat_session.id,
                user_query=query,
                system_answer=rag_result.get('answer', ''),
                references=rag_result.get('references', []),
                response_time=rag_result.get('total_time_ms', 0)
            )
            session.add(message)
            session.flush()

            # 3. 更新会话时间
            chat_session.updated_at = datetime.now()

            session.commit()

            logger.info(f"问答记录保存成功：session={chat_session.id}, message={message.id}")

            return {
                'success': True,
                'session_id': chat_session.id,
                'message_id': message.id
            }

        except Exception as e:
            session.rollback()
            logger.error(f"保存问答记录失败：{str(e)}", exc_info=True)
            return {
                'success': False,
                'error': '保存问答记录失败',
                'error_internal': str(e)
            }
        finally:
            session.close()

    @staticmethod
    def get_session_messages(session_id: int, user_id: int) -> list:
        """
        获取会话的消息列表（验证权限）
        :param session_id: 会话ID
        :param user_id: 用户ID
        :return: 消息列表
        """
        session = get_session()
        try:
            # 验证会话归属
            chat_session = session.query(ChatSession).filter_by(
                id=session_id,
                user_id=user_id
            ).first()

            if not chat_session:
                return []

            messages = session.query(ChatMessage).filter_by(
                session_id=session_id
            ).order_by(ChatMessage.created_at).all()

            return [{
                'id': msg.id,
                'user_query': msg.user_query,
                'system_answer': msg.system_answer,
                'references': msg.references,
                'response_time': msg.response_time,
                'created_at': msg.created_at
            } for msg in messages]

        finally:
            session.close()

    @staticmethod
    def get_user_sessions(user_id: int, limit: int = 50) -> list:
        """
        获取用户的会话列表
        :param user_id: 用户ID
        :param limit: 返回数量限制
        :return: 会话列表
        """
        session = get_session()
        try:
            sessions = session.query(ChatSession).filter_by(
                user_id=user_id
            ).order_by(ChatSession.updated_at.desc()).limit(limit).all()

            result = []
            for chat_session in sessions:
                # 获取第一条消息作为标题
                first_message = session.query(ChatMessage).filter_by(
                    session_id=chat_session.id
                ).order_by(ChatMessage.created_at).first()

                # 获取消息数量
                message_count = session.query(ChatMessage).filter_by(
                    session_id=chat_session.id
                ).count()

                # 生成标题
                if chat_session.title:
                    title = chat_session.title
                elif first_message:
                    # 截取前30个字符作为标题
                    title = first_message.user_query[:30] + ('...' if len(first_message.user_query) > 30 else '')
                else:
                    title = "空会话"

                result.append({
                    'id': chat_session.id,
                    'title': title,
                    'message_count': message_count,
                    'created_at': chat_session.created_at,
                    'updated_at': chat_session.updated_at
                })

            return result

        finally:
            session.close()

    @staticmethod
    def update_session_title(session_id: int, user_id: int, title: str) -> dict:
        """
        更新会话标题
        :param session_id: 会话ID
        :param user_id: 用户ID
        :param title: 新标题
        :return: {success, message}
        """
        session = get_session()
        try:
            chat_session = session.query(ChatSession).filter_by(
                id=session_id,
                user_id=user_id
            ).first()

            if not chat_session:
                return {'success': False, 'message': '会话不存在'}

            chat_session.title = title
            session.commit()

            return {'success': True, 'message': '标题更新成功'}

        except Exception as e:
            session.rollback()
            logger.error(f"更新会话标题失败：{str(e)}", exc_info=True)
            return {'success': False, 'message': '更新失败'}
        finally:
            session.close()
