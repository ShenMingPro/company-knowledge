"""
审计日志页面：管理员查看和筛选审计日志
"""
import streamlit as st
from datetime import datetime, timedelta
from services.audit_service import AuditService
from services.permission_service import PermissionService


def show_audit_logs_page():
    """显示审计日志页面（仅管理员）"""

    user = st.session_state.get("user")
    if not user:
        st.error("请先登录")
        return

    # 权限检查
    if not PermissionService.can_manage_documents(user["role"]):
        st.error("您没有权限访问审计日志")
        return

    st.title("📊 审计日志")

    # ==================== 筛选条件 ====================
    with st.expander("🔍 筛选条件", expanded=True):
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            # 操作类型筛选
            action_types = [
                "全部",
                "rag_query",
                "document_upload",
                "document_new_version",
                "document_reindex",
                "document_deactivate",
                "document_restore",
                "document_delete",
                "feedback_submit"
            ]
            selected_action = st.selectbox("操作类型", action_types)

        with col2:
            # 成功状态筛选
            success_options = ["全部", "成功", "失败"]
            selected_success = st.selectbox("执行状态", success_options)

        with col3:
            # 用户筛选
            user_id_input = st.text_input("用户ID（选填）", placeholder="留空显示全部")

        with col4:
            # 时间范围
            time_ranges = ["最近1小时", "最近24小时", "最近7天", "最近30天", "全部"]
            selected_range = st.selectbox("时间范围", time_ranges)

    # 构建筛选条件
    filters = {}

    if selected_action != "全部":
        filters["action_type"] = selected_action

    if selected_success == "成功":
        filters["is_success"] = True
    elif selected_success == "失败":
        filters["is_success"] = False

    if user_id_input and user_id_input.strip().isdigit():
        filters["user_id"] = int(user_id_input.strip())

    # 时间范围
    if selected_range == "最近1小时":
        filters["start_time"] = datetime.now() - timedelta(hours=1)
    elif selected_range == "最近24小时":
        filters["start_time"] = datetime.now() - timedelta(days=1)
    elif selected_range == "最近7天":
        filters["start_time"] = datetime.now() - timedelta(days=7)
    elif selected_range == "最近30天":
        filters["start_time"] = datetime.now() - timedelta(days=30)

    # ==================== 查询审计日志 ====================
    if "audit_page" not in st.session_state:
        st.session_state["audit_page"] = 1

    page_size = 20
    result = AuditService.list_logs(
        user_role=user["role"],
        filters=filters,
        page=st.session_state["audit_page"],
        page_size=page_size
    )

    if not result["success"]:
        st.error(result.get("error", "查询失败"))
        return

    logs = result["logs"]
    total = result["total"]

    # ==================== 显示统计信息 ====================
    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总记录数", total)
    with col2:
        success_count = sum(1 for log in logs if log["is_success"])
        st.metric("成功", success_count)
    with col3:
        fail_count = sum(1 for log in logs if not log["is_success"])
        st.metric("失败", fail_count)
    with col4:
        rag_count = sum(1 for log in logs if log["action_type"] == "rag_query")
        st.metric("RAG查询", rag_count)

    # ==================== 显示日志列表 ====================
    st.markdown("---")
    st.subheader(f"日志列表 (第{st.session_state['audit_page']}页，共{(total + page_size - 1) // page_size}页)")

    if not logs:
        st.info("暂无日志记录")
        return

    # 表格展示
    for log in logs:
        with st.container():
            # 头部信息
            col1, col2, col3, col4 = st.columns([2, 2, 2, 1])

            with col1:
                st.markdown(f"**{log['created_at'].strftime('%Y-%m-%d %H:%M:%S')}**")

            with col2:
                st.markdown(f"👤 {log['username']} ({log['user_role']})")

            with col3:
                action_label = {
                    'rag_query': '💬 RAG查询',
                    'document_upload': '📤 文档上传',
                    'document_new_version': '🔄 新版本',
                    'document_reindex': '🔁 重新索引',
                    'document_deactivate': '⏸️ 下架',
                    'document_restore': '▶️ 恢复',
                    'document_delete': '🗑️ 删除',
                    'feedback_submit': '👍 反馈'
                }.get(log['action_type'], log['action_type'])
                st.markdown(action_label)

            with col4:
                if log["is_success"]:
                    st.success("✅ 成功")
                else:
                    st.error("❌ 失败")

            # 详细信息
            if log["action_type"] == "rag_query":
                # RAG查询详情
                col1, col2 = st.columns([3, 1])

                with col1:
                    if log["query_summary"]:
                        st.caption(f"**问题：** {log['query_summary']}")

                    # 回答状态
                    if log["has_answer"] is True:
                        st.caption("✅ 已回答")
                    elif log["has_answer"] is False:
                        refusal_labels = {
                            'no_retrieval_result': '无检索结果',
                            'below_relevance_threshold': '低于相关性阈值',
                            'permission_filtered': '权限过滤',
                            'invalid_document_version': '文档版本无效',
                            'generation_error': '生成异常'
                        }
                        refusal_text = refusal_labels.get(log["refusal_reason"], log["refusal_reason"])
                        st.caption(f"🚫 拒答：{refusal_text}")

                with col2:
                    if log["response_time"]:
                        st.caption(f"⏱️ {log['response_time']}ms")
                    if log["model_name"]:
                        st.caption(f"🤖 {log['model_name']}")
                    if log["reference_count"] > 0:
                        st.caption(f"📎 {log['reference_count']}条引用")

                # 检索指标
                if log["retrieved_count"] > 0:
                    st.caption(
                        f"📊 检索 {log['retrieved_count']} → "
                        f"过滤 {log['filtered_count']} → "
                        f"有效 {log['valid_count']}"
                    )

            else:
                # 文档操作详情
                st.caption(f"**操作对象：** {log['action_target']}")

            # 错误信息
            if log["error_message"]:
                st.caption(f"⚠️ **错误：** {log['error_message']}")

            st.divider()

    # ==================== 分页控制 ====================
    col1, col2, col3 = st.columns([1, 2, 1])

    with col1:
        if st.session_state["audit_page"] > 1:
            if st.button("⬅️ 上一页"):
                st.session_state["audit_page"] -= 1
                st.rerun()

    with col2:
        st.markdown(f"<center>第 {st.session_state['audit_page']} / {(total + page_size - 1) // page_size} 页</center>", unsafe_allow_html=True)

    with col3:
        if st.session_state["audit_page"] * page_size < total:
            if st.button("下一页 ➡️"):
                st.session_state["audit_page"] += 1
                st.rerun()


if __name__ == "__main__":
    # 测试页面
    show_audit_logs_page()
