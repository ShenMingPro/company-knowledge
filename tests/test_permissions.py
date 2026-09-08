"""
权限测试 - 验证不同角色的权限控制
"""
import os
import sys
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.permission_service import PermissionService
from services.auth_service import AuthService
from models.database import init_database, get_session, User


def setup_test_users():
    """设置测试用户"""
    init_database()

    # 确保三种角色的用户存在
    users = [
        {'username': 'test_employee', 'password': 'test123', 'role': 'employee'},
        {'username': 'test_service', 'password': 'test123', 'role': 'service'},
        {'username': 'test_admin', 'password': 'test123', 'role': 'admin'},
    ]

    for user_data in users:
        session = get_session()
        try:
            existing = session.query(User).filter_by(username=user_data['username']).first()
            if not existing:
                AuthService.create_user(
                    user_data['username'],
                    user_data['password'],
                    user_data['role']
                )
        finally:
            session.close()


def test_permission_service():
    """测试权限服务"""
    print("\n" + "="*60)
    print("权限控制测试")
    print("="*60)

    setup_test_users()

    print("\n测试1: 角色名称显示")
    print("-" * 60)

    assert PermissionService.get_role_name('employee') == '普通员工'
    assert PermissionService.get_role_name('service') == '客服'
    assert PermissionService.get_role_name('admin') == '管理员'
    print("✓ 角色名称映射正确")

    print("\n测试2: 普通员工权限")
    print("-" * 60)

    spaces = PermissionService.get_accessible_spaces('employee')
    space_codes = [s['code'] for s in spaces]
    print(f"  可访问空间: {space_codes}")

    assert 'product' in space_codes, "应该可访问产品知识库"
    assert 'policy' in space_codes, "应该可访问企业制度库"
    assert 'service' not in space_codes, "不应访问售后知识库"
    assert 'admin' not in space_codes, "不应访问管理员空间"

    assert not PermissionService.can_manage_documents('employee'), "不能管理文档"
    assert not PermissionService.can_view_audit_logs('employee'), "不能查看审计日志"
    assert not PermissionService.can_generate_service_reply('employee'), "不能生成客服回复"

    print("✓ 普通员工权限正确")

    print("\n测试3: 客服权限")
    print("-" * 60)

    spaces = PermissionService.get_accessible_spaces('service')
    space_codes = [s['code'] for s in spaces]
    print(f"  可访问空间: {space_codes}")

    assert 'product' in space_codes, "应该可访问产品知识库"
    assert 'policy' in space_codes, "应该可访问企业制度库"
    assert 'service' in space_codes, "应该可访问售后知识库"
    assert 'admin' not in space_codes, "不应访问管理员空间"

    assert not PermissionService.can_manage_documents('service'), "不能管理文档"
    assert not PermissionService.can_view_audit_logs('service'), "不能查看审计日志"
    assert PermissionService.can_generate_service_reply('service'), "可以生成客服回复"

    print("✓ 客服权限正确")

    print("\n测试4: 管理员权限")
    print("-" * 60)

    spaces = PermissionService.get_accessible_spaces('admin')
    space_codes = [s['code'] for s in spaces]
    print(f"  可访问空间: {space_codes}")

    assert 'product' in space_codes, "应该可访问产品知识库"
    assert 'policy' in space_codes, "应该可访问企业制度库"
    assert 'service' in space_codes, "应该可访问售后知识库"
    assert 'admin' in space_codes, "应该可访问管理员空间"

    assert PermissionService.can_manage_documents('admin'), "可以管理文档"
    assert PermissionService.can_view_audit_logs('admin'), "可以查看审计日志"
    assert PermissionService.can_generate_service_reply('admin'), "可以生成客服回复"

    print("✓ 管理员权限正确")

    print("\n测试5: 知识空间访问控制")
    print("-" * 60)

    space_ids = PermissionService.get_accessible_space_ids('employee')
    print(f"  普通员工可访问空间ID: {space_ids}")
    assert len(space_ids) == 2, "普通员工应该能访问2个空间"

    space_ids = PermissionService.get_accessible_space_ids('service')
    print(f"  客服可访问空间ID: {space_ids}")
    assert len(space_ids) == 3, "客服应该能访问3个空间"

    space_ids = PermissionService.get_accessible_space_ids('admin')
    print(f"  管理员可访问空间ID: {space_ids}")
    assert len(space_ids) == 4, "管理员应该能访问4个空间"

    print("✓ 空间访问控制正确")

    print("\n测试6: 用户登录验证")
    print("-" * 60)

    # 测试正确的登录
    result = AuthService.login('test_employee', 'test123')
    assert result['success'], "正确的用户名和密码应该登录成功"
    assert result['user']['role'] == 'employee', "角色应该是employee"
    print("✓ 正确登录成功")

    # 测试错误的密码
    result = AuthService.login('test_employee', 'wrong_password')
    assert not result['success'], "错误的密码应该登录失败"
    print("✓ 错误密码被拒绝")

    # 测试不存在的用户
    result = AuthService.login('nonexistent_user', 'test123')
    assert not result['success'], "不存在的用户应该登录失败"
    print("✓ 不存在的用户被拒绝")

    print("\n" + "="*60)
    print("✓ 权限控制测试全部通过！")
    print("="*60)
    return True


if __name__ == "__main__":
    try:
        success = test_permission_service()
        sys.exit(0 if success else 1)
    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 测试出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
