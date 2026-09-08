"""
权限服务
"""
from models.database import KnowledgeSpace, get_session


class PermissionService:
    """权限服务"""

    # 角色显示名称
    ROLE_NAMES = {
        'employee': '普通员工',
        'service': '客服',
        'admin': '管理员'
    }

    @staticmethod
    def get_role_name(role: str) -> str:
        """获取角色显示名称"""
        return PermissionService.ROLE_NAMES.get(role, role)

    @staticmethod
    def get_accessible_spaces(role: str) -> list:
        """
        获取指定角色可访问的知识空间列表
        :param role: 角色（employee/service/admin）
        :return: 知识空间列表
        """
        session = get_session()
        try:
            spaces = session.query(KnowledgeSpace).filter_by(is_enabled=True).all()
            accessible = []

            for space in spaces:
                allowed_roles = [r.strip() for r in space.allowed_roles.split(',')]
                if role in allowed_roles:
                    accessible.append({
                        'id': space.id,
                        'name': space.name,
                        'code': space.code,
                        'description': space.description
                    })

            return accessible
        finally:
            session.close()

    @staticmethod
    def get_accessible_space_ids(role: str) -> list:
        """
        获取指定角色可访问的知识空间ID列表
        :param role: 角色
        :return: 知识空间ID列表
        """
        spaces = PermissionService.get_accessible_spaces(role)
        return [space['id'] for space in spaces]

    @staticmethod
    def can_access_space(role: str, space_id: int) -> bool:
        """
        检查角色是否可以访问指定知识空间
        :param role: 角色
        :param space_id: 知识空间ID
        :return: 是否可以访问
        """
        accessible_ids = PermissionService.get_accessible_space_ids(role)
        return space_id in accessible_ids

    @staticmethod
    def can_manage_documents(role: str) -> bool:
        """检查角色是否可以管理文档"""
        return role == 'admin'

    @staticmethod
    def can_view_audit_logs(role: str) -> bool:
        """检查角色是否可以查看审计日志"""
        return role == 'admin'

    @staticmethod
    def can_generate_service_reply(role: str) -> bool:
        """检查角色是否可以生成客服回复"""
        return role in ['service', 'admin']
