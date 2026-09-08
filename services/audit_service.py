"""
审计服务：操作审计日志记录和查询
"""
import re
from datetime import datetime
from typing import Dict, List, Optional
from models.database import get_session, AuditLog, User
from services.permission_service import PermissionService
from utils.logger_handler import logger


class AuditService:
    """审计服务"""

    # 敏感信息模式（用于脱敏）
    SENSITIVE_PATTERNS = [
        (r'api[_-]?key[=:\s]+[\w\-]{10,}', '[API_KEY]'),
        (r'password[=:\s]+\S+', '[PASSWORD]'),
        (r'token[=:\s]+[\w\-\.]{10,}', '[TOKEN]'),
        (r'secret[=:\s]+\S+', '[SECRET]'),
        (r'bearer\s+[\w\-\.]+', '[BEARER_TOKEN]'),
    ]

    @staticmethod
    def sanitize_text(text: str, max_length: int = 200) -> str:
        """
        脱敏并截断文本
        :param text: 原始文本
        :param max_length: 最大长度
        :return: 脱敏后的文本
        """
        if not text:
            return ''

        # 脱敏
        sanitized = text
        for pattern, replacement in AuditService.SENSITIVE_PATTERNS:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)

        # 截断
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + '...'

        return sanitized

    @staticmethod
    def sanitize_error(error_message: str) -> str:
        """
        清洗错误信息（移除堆栈和内部路径）
        :param error_message: 原始错误
        :return: 清洗后的错误
        """
        if not error_message:
            return ''

        # 只保留第一行错误描述，去除堆栈
        lines = error_message.split('\n')
        error_line = lines[0] if lines else error_message

        # 移除文件路径
        error_line = re.sub(r'File ".*?"', 'File "[...]"', error_line)

        # 截断
        return error_line[:500]

    @staticmethod
    def build_retrieved_docs_json(rag_result: dict) -> dict:
        """
        构建审计用的检索文档JSON（不包含正文）
        :param rag_result: RAG返回结果
        :return: 审计JSON结构
        """
        # 提取引用元数据（不包含content字段）
        references = []
        for ref in rag_result.get('references', []):
            references.append({
                'document_id': ref.get('document_id'),
                'document_version_id': ref.get('document_version_id'),
                'source_name': ref.get('source_name'),
                'page': ref.get('page'),
                'section': ref.get('section'),
                'chunk_index': ref.get('chunk_index'),
                'relevance_score': ref.get('relevance_score')
            })

        return {
            'schema_version': 1,
            'has_answer': rag_result.get('has_answer', False),
            'refusal_reason': rag_result.get('refusal_reason'),
            'metrics': {
                'retrieved_count': rag_result.get('retrieved_count', 0),
                'filtered_count': rag_result.get('filtered_count', 0),
                'valid_count': rag_result.get('valid_count', 0),
                'retrieval_time_ms': rag_result.get('retrieval_time_ms', 0),
                'generation_time_ms': rag_result.get('generation_time_ms', 0)
            },
            'references': references
        }

    @staticmethod
    def record_rag_query(user_id: int, message_id: int, query: str, rag_result: dict) -> dict:
        """
        记录RAG查询审计
        :param user_id: 用户ID
        :param message_id: 消息ID
        :param query: 用户问题
        :param rag_result: RAG返回结果
        :return: {success, audit_log_id, error}
        """
        session = get_session()
        try:
            # 判断技术执行是否成功
            # 正常拒答属于成功执行，只有异常才是失败
            is_success = rag_result.get('refusal_reason') != 'generation_error'
            error_message = None

            if not is_success:
                # 只保存清洗后的错误
                error_internal = rag_result.get('error_internal', '')
                error_message = AuditService.sanitize_error(error_internal)

            # 创建审计记录
            audit_log = AuditLog(
                user_id=user_id,
                action_type='rag_query',
                action_target=f'chat_message:{message_id}',
                query_summary=AuditService.sanitize_text(query, max_length=200),
                retrieved_docs=AuditService.build_retrieved_docs_json(rag_result),
                model_name=rag_result.get('model_name'),
                response_time=rag_result.get('total_time_ms', 0),
                is_success=is_success,
                error_message=error_message
            )

            session.add(audit_log)
            session.commit()

            logger.info(f"RAG查询审计记录成功：audit_log={audit_log.id}, message={message_id}")

            return {
                'success': True,
                'audit_log_id': audit_log.id
            }

        except Exception as e:
            session.rollback()
            logger.error(f"记录RAG审计失败：{str(e)}", exc_info=True)
            return {
                'success': False,
                'error': '记录审计失败'
            }
        finally:
            session.close()

    @staticmethod
    def record_document_operation(user_id: int, action_type: str, document_id: int,
                                  version_id: int = None, space_id: int = None,
                                  is_success: bool = True, error_message: str = None) -> dict:
        """
        记录文档操作审计
        :param user_id: 用户ID
        :param action_type: 操作类型（document_upload/document_new_version/document_reindex等）
        :param document_id: 文档ID
        :param version_id: 版本ID
        :param space_id: 知识空间ID
        :param is_success: 是否成功
        :param error_message: 错误信息
        :return: {success, audit_log_id}
        """
        session = get_session()
        try:
            # 构建操作目标
            action_target = f'document:{document_id}'
            if version_id:
                action_target += f'/version:{version_id}'

            # 构建元数据
            retrieved_docs = {
                'schema_version': 1,
                'document_id': document_id,
                'version_id': version_id,
                'space_id': space_id
            }

            audit_log = AuditLog(
                user_id=user_id,
                action_type=action_type,
                action_target=action_target,
                retrieved_docs=retrieved_docs,
                is_success=is_success,
                error_message=AuditService.sanitize_error(error_message) if error_message else None
            )

            session.add(audit_log)
            session.commit()

            logger.info(f"文档操作审计记录成功：{action_type}, document={document_id}")

            return {
                'success': True,
                'audit_log_id': audit_log.id
            }

        except Exception as e:
            session.rollback()
            logger.error(f"记录文档操作审计失败：{str(e)}", exc_info=True)
            return {
                'success': False,
                'error': '记录审计失败'
            }
        finally:
            session.close()

    @staticmethod
    def list_logs(user_role: str, filters: dict = None, page: int = 1, page_size: int = 50) -> dict:
        """
        查询审计日志列表（仅管理员）
        :param user_role: 用户角色
        :param filters: 筛选条件 {user_id, action_type, is_success, start_time, end_time}
        :param page: 页码
        :param page_size: 每页数量
        :return: {success, logs, total, error}
        """
        # 权限验证
        if not PermissionService.can_manage_documents(user_role):
            logger.warning(f"非管理员尝试查询审计日志：role={user_role}")
            return {
                'success': False,
                'error': '权限不足',
                'logs': [],
                'total': 0
            }

        session = get_session()
        try:
            query = session.query(AuditLog)

            # 应用筛选
            if filters:
                if filters.get('user_id'):
                    query = query.filter(AuditLog.user_id == filters['user_id'])

                if filters.get('action_type'):
                    query = query.filter(AuditLog.action_type == filters['action_type'])

                if filters.get('is_success') is not None:
                    query = query.filter(AuditLog.is_success == filters['is_success'])

                if filters.get('start_time'):
                    query = query.filter(AuditLog.created_at >= filters['start_time'])

                if filters.get('end_time'):
                    query = query.filter(AuditLog.created_at <= filters['end_time'])

            # 总数
            total = query.count()

            # 分页（时间倒序）
            logs = query.order_by(AuditLog.created_at.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size).all()

            # 格式化结果
            result_logs = []
            for log in logs:
                # 获取用户信息
                user = session.query(User).filter_by(id=log.user_id).first()

                # 提取关键信息
                retrieved_docs = log.retrieved_docs or {}
                has_answer = retrieved_docs.get('has_answer', None)
                refusal_reason = retrieved_docs.get('refusal_reason')
                metrics = retrieved_docs.get('metrics', {})
                references = retrieved_docs.get('references', [])

                result_logs.append({
                    'id': log.id,
                    'created_at': log.created_at,
                    'user_id': log.user_id,
                    'username': user.username if user else '未知',
                    'user_role': user.role if user else '未知',
                    'action_type': log.action_type,
                    'action_target': log.action_target,
                    'query_summary': log.query_summary,
                    'is_success': log.is_success,
                    'has_answer': has_answer,
                    'refusal_reason': refusal_reason,
                    'model_name': log.model_name,
                    'response_time': log.response_time,
                    'retrieved_count': metrics.get('retrieved_count', 0),
                    'filtered_count': metrics.get('filtered_count', 0),
                    'valid_count': metrics.get('valid_count', 0),
                    'reference_count': len(references),
                    'error_message': log.error_message
                })

            return {
                'success': True,
                'logs': result_logs,
                'total': total,
                'page': page,
                'page_size': page_size
            }

        except Exception as e:
            logger.error(f"查询审计日志失败：{str(e)}", exc_info=True)
            return {
                'success': False,
                'error': '查询失败',
                'logs': [],
                'total': 0
            }
        finally:
            session.close()

    @staticmethod
    def get_log_detail(user_role: str, log_id: int) -> dict:
        """
        获取审计日志详情（仅管理员）
        :param user_role: 用户角色
        :param log_id: 日志ID
        :return: {success, log, error}
        """
        # 权限验证
        if not PermissionService.can_manage_documents(user_role):
            return {
                'success': False,
                'error': '权限不足'
            }

        session = get_session()
        try:
            log = session.query(AuditLog).filter_by(id=log_id).first()
            if not log:
                return {
                    'success': False,
                    'error': '日志不存在'
                }

            user = session.query(User).filter_by(id=log.user_id).first()

            return {
                'success': True,
                'log': {
                    'id': log.id,
                    'created_at': log.created_at,
                    'user_id': log.user_id,
                    'username': user.username if user else '未知',
                    'user_role': user.role if user else '未知',
                    'action_type': log.action_type,
                    'action_target': log.action_target,
                    'query_summary': log.query_summary,
                    'retrieved_docs': log.retrieved_docs,
                    'model_name': log.model_name,
                    'response_time': log.response_time,
                    'is_success': log.is_success,
                    'error_message': log.error_message
                }
            }

        finally:
            session.close()
