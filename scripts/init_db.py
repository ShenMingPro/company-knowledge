"""
数据库初始化脚本
创建表结构、初始化知识空间和演示账号
"""
import os
import sys
import io

# 设置stdout编码为UTF-8（解决Windows控制台编码问题）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import init_database, get_session, User, KnowledgeSpace
from services.auth_service import AuthService
from dotenv import load_dotenv

load_dotenv()


def init_knowledge_spaces():
    """初始化知识空间"""
    session = get_session()
    try:
        # 检查是否已经初始化
        existing = session.query(KnowledgeSpace).first()
        if existing:
            print("知识空间已存在，跳过初始化")
            return

        spaces = [
            {
                "name": "产品知识库",
                "code": "product",
                "description": "产品功能、使用方法、常见问题",
                "allowed_roles": "employee,service,admin"
            },
            {
                "name": "企业制度库",
                "code": "policy",
                "description": "企业制度、流程规范、培训资料",
                "allowed_roles": "employee,service,admin"
            },
            {
                "name": "售后知识库",
                "code": "service",
                "description": "故障排查、维修SOP、保修政策",
                "allowed_roles": "service,admin"
            },
            {
                "name": "管理员空间",
                "code": "admin",
                "description": "内部管理文档",
                "allowed_roles": "admin"
            }
        ]

        for space_data in spaces:
            space = KnowledgeSpace(**space_data)
            session.add(space)

        session.commit()
        print("[OK] 知识空间初始化成功")
    except Exception as e:
        session.rollback()
        print(f"[ERROR] 知识空间初始化失败：{str(e)}")
    finally:
        session.close()


def init_demo_users():
    """初始化演示账号"""
    users = [
        {
            "username": "employee",
            "password": os.getenv("DEMO_EMPLOYEE_PASSWORD", "employee123"),
            "role": "employee"
        },
        {
            "username": "service",
            "password": os.getenv("DEMO_SERVICE_PASSWORD", "service123"),
            "role": "service"
        },
        {
            "username": "admin",
            "password": os.getenv("DEMO_ADMIN_PASSWORD", "admin123"),
            "role": "admin"
        }
    ]

    for user_data in users:
        result = AuthService.create_user(
            username=user_data["username"],
            password=user_data["password"],
            role=user_data["role"]
        )
        if result["success"]:
            print(f"[OK] 创建用户 {user_data['username']} ({user_data['role']})")
        else:
            print(f"[ERROR] 创建用户 {user_data['username']} 失败：{result['message']}")


def main():
    """主函数"""
    print("=" * 50)
    print("KnowBase Lite 数据库初始化")
    print("=" * 50)

    # 1. 创建数据库表
    print("\n[1/3] 创建数据库表...")
    init_database()

    # 2. 初始化知识空间
    print("\n[2/3] 初始化知识空间...")
    init_knowledge_spaces()

    # 3. 初始化演示账号
    print("\n[3/3] 初始化演示账号...")
    init_demo_users()

    print("\n" + "=" * 50)
    print("初始化完成！")
    print("=" * 50)
    print("\n演示账号：")
    print("  普通员工 - 用户名: employee, 密码: 见.env配置")
    print("  客服     - 用户名: service,  密码: 见.env配置")
    print("  管理员   - 用户名: admin,    密码: 见.env配置")
    print("\n启动命令：streamlit run app.py")
    print("=" * 50)


if __name__ == "__main__":
    main()
