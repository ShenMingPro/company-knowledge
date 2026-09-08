"""
用户认证服务
"""
import bcrypt
from models.database import User, get_session


class AuthService:
    """用户认证服务"""

    @staticmethod
    def hash_password(password: str) -> str:
        """密码哈希"""
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """验证密码"""
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

    @staticmethod
    def login(username: str, password: str) -> dict:
        """
        用户登录
        :param username: 用户名
        :param password: 密码
        :return: 登录结果字典
        """
        session = get_session()
        try:
            user = session.query(User).filter_by(username=username).first()

            if not user:
                return {"success": False, "message": "用户名或密码错误"}

            if not user.is_enabled:
                return {"success": False, "message": "账号已被禁用"}

            if not AuthService.verify_password(password, user.password_hash):
                return {"success": False, "message": "用户名或密码错误"}

            return {
                "success": True,
                "message": "登录成功",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "role": user.role
                }
            }
        finally:
            session.close()

    @staticmethod
    def create_user(username: str, password: str, role: str) -> dict:
        """
        创建用户
        :param username: 用户名
        :param password: 密码
        :param role: 角色（employee/service/admin）
        :return: 创建结果
        """
        if role not in ['employee', 'service', 'admin']:
            return {"success": False, "message": "无效的角色"}

        session = get_session()
        try:
            # 检查用户名是否已存在
            existing = session.query(User).filter_by(username=username).first()
            if existing:
                return {"success": False, "message": "用户名已存在"}

            # 创建用户
            user = User(
                username=username,
                password_hash=AuthService.hash_password(password),
                role=role
            )
            session.add(user)
            session.commit()

            return {"success": True, "message": "用户创建成功", "user_id": user.id}
        except Exception as e:
            session.rollback()
            return {"success": False, "message": f"创建失败：{str(e)}"}
        finally:
            session.close()

    @staticmethod
    def get_user_by_id(user_id: int) -> User:
        """根据ID获取用户"""
        session = get_session()
        try:
            return session.query(User).filter_by(id=user_id).first()
        finally:
            session.close()
