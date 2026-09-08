"""
文档管理页面组件
"""
import streamlit as st
from services.document_service import DocumentService
from services.permission_service import PermissionService
from services.audit_service import AuditService
from rag.vector_store import VectorStoreService
from models.database import get_session, KnowledgeSpace
import os


def show_document_management_page():
    """显示文档管理页面"""
    st.header("📁 文档管理")

    # 统计信息
    all_docs = DocumentService.get_documents(include_deleted=False)
    success_count = sum(1 for d in all_docs if d['version_status'] == 'success')
    failed_count = sum(1 for d in all_docs if d['version_status'] == 'failed')
    inactive_count = sum(1 for d in all_docs if d['version_status'] == 'inactive')

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("文档总数", len(all_docs))
    with col2:
        st.metric("有效文档", success_count)
    with col3:
        st.metric("索引失败", failed_count)
    with col4:
        st.metric("已下架", inactive_count)

    st.divider()

    # 筛选区
    col1, col2, col3 = st.columns(3)

    with col1:
        search_name = st.text_input("🔍 搜索文档名", key="search_name")

    with col2:
        session = get_session()
        try:
            spaces = session.query(KnowledgeSpace).filter_by(is_enabled=True).all()
            space_options = {"全部": None}
            space_options.update({s.name: s.id for s in spaces})
        finally:
            session.close()

        selected_space = st.selectbox("知识空间", options=list(space_options.keys()), key="filter_space")
        space_id_filter = space_options[selected_space]

    with col3:
        status_options = {
            "全部": None,
            "成功": "success",
            "失败": "failed",
            "处理中": "processing",
            "已下架": "inactive"
        }
        selected_status = st.selectbox("状态", options=list(status_options.keys()), key="filter_status")
        status_filter = status_options[selected_status]

    # 获取文档列表
    documents = DocumentService.get_documents(
        space_id=space_id_filter,
        include_deleted=False,
        search_name=search_name if search_name else None,
        status_filter=status_filter
    )

    st.divider()

    # 文档列表
    if not documents:
        st.info("暂无文档")
    else:
        st.markdown(f"### 文档列表 ({len(documents)})")

        for doc in documents:
            with st.expander(f"📄 {doc['name']} - v{doc['version_number']} ({doc['version_status']})"):
                col1, col2 = st.columns([2, 1])

                with col1:
                    st.markdown(f"**知识空间：** {doc['space_name']}")
                    st.markdown(f"**当前版本：** {doc['version_number']}")
                    st.markdown(f"**状态：** {doc['version_status']}")
                    st.markdown(f"**分片数：** {doc['chunk_count']}")
                    st.markdown(f"**文件大小：** {doc['file_size']} 字节")
                    st.markdown(f"**上传人：** {doc['created_by']}")
                    st.markdown(f"**创建时间：** {doc['created_at']}")
                    if doc['indexed_at']:
                        st.markdown(f"**索引时间：** {doc['indexed_at']}")
                    if doc['error_message']:
                        st.error(f"错误信息：{doc['error_message']}")

                with col2:
                    # 操作按钮
                    if st.button("📖 查看详情", key=f"detail_{doc['id']}", use_container_width=True):
                        st.session_state[f"show_detail_{doc['id']}"] = True
                        st.rerun()

                    if doc['version_status'] in ['success', 'failed', 'inactive']:
                        if st.button("🔄 重新索引", key=f"reindex_{doc['id']}", use_container_width=True):
                            show_reindex_document(doc['id'])

                    if doc['version_status'] == 'success':
                        if st.button("⏸️ 下架", key=f"deactivate_{doc['id']}", use_container_width=True):
                            show_deactivate_document(doc['id'])

                    if doc['version_status'] == 'inactive':
                        if st.button("▶️ 恢复", key=f"restore_{doc['id']}", use_container_width=True):
                            show_restore_document(doc['id'])

                    if st.button("🗑️ 删除", key=f"delete_{doc['id']}", use_container_width=True, type="secondary"):
                        st.session_state[f"confirm_delete_{doc['id']}"] = True

                # 确认删除
                if st.session_state.get(f"confirm_delete_{doc['id']}", False):
                    st.warning("⚠️ 确认删除该文档？")
                    col_yes, col_no = st.columns(2)
                    with col_yes:
                        if st.button("确认删除", key=f"confirm_yes_{doc['id']}", type="primary"):
                            result = DocumentService.soft_delete_document(doc['id'])

                            # 记录审计
                            user_id = st.session_state["user"]["id"]
                            AuditService.record_document_operation(
                                user_id=user_id,
                                action_type='document_delete',
                                document_id=doc['id'],
                                version_id=doc.get('current_version_id'),
                                space_id=doc['space_id'],
                                is_success=result['success'],
                                error_message=result.get('message') if not result['success'] else None
                            )

                            if result['success']:
                                st.success(result['message'])
                                st.session_state[f"confirm_delete_{doc['id']}"] = False
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error(result['message'])
                    with col_no:
                        if st.button("取消", key=f"confirm_no_{doc['id']}"):
                            st.session_state[f"confirm_delete_{doc['id']}"] = False
                            st.rerun()

                # 显示文档详情
                if st.session_state.get(f"show_detail_{doc['id']}", False):
                    show_document_detail_dialog(doc['id'])


def show_document_detail_dialog(document_id: int):
    """显示文档详情对话框"""
    detail = DocumentService.get_document_detail(document_id)

    if not detail['success']:
        st.error(detail['message'])
        return

    doc = detail['document']
    versions = detail['versions']

    st.markdown("---")
    st.markdown("### 📋 文档详情")

    st.markdown(f"**文档名称：** {doc['name']}")
    st.markdown(f"**知识空间：** {doc['space_name']}")
    st.markdown(f"**上传人：** {doc['created_by']}")
    st.markdown(f"**创建时间：** {doc['created_at']}")

    st.markdown("### 📜 版本历史")

    for v in versions:
        status_emoji = {
            'success': '✅',
            'failed': '❌',
            'inactive': '⏸️',
            'processing': '⏳',
            'pending': '⏱️',
            'deleted': '🗑️'
        }.get(v['status'], '❓')

        current_mark = " (当前)" if v['is_current'] else ""

        with st.expander(f"{status_emoji} 版本 {v['version_number']}{current_mark} - {v['status']}"):
            st.markdown(f"**文件哈希：** {v['file_hash'][:16]}...")
            st.markdown(f"**文件路径：** {v['file_path']}")
            st.markdown(f"**文件大小：** {v['file_size']} 字节")
            st.markdown(f"**分片数：** {v['chunk_count']}")
            st.markdown(f"**创建时间：** {v['created_at']}")
            if v['indexed_at']:
                st.markdown(f"**索引时间：** {v['indexed_at']}")
            if v['error_message']:
                st.error(f"**错误信息：** {v['error_message']}")

    if st.button("关闭", key=f"close_detail_{document_id}"):
        st.session_state[f"show_detail_{document_id}"] = False
        st.rerun()


def show_reindex_document(document_id: int):
    """重新索引文档"""
    detail = DocumentService.get_document_detail(document_id)
    if not detail['success']:
        st.error(detail['message'])
        return

    doc = detail['document']
    current_version = next((v for v in detail['versions'] if v['is_current']), None)

    if not current_version:
        st.error("没有找到当前版本")
        return

    with st.spinner("重新索引中..."):
        # 准备重新索引
        result = DocumentService.reindex_version(current_version['id'])

        if not result['success']:
            st.error(result['message'])
            return

        # 执行索引
        vector_service = VectorStoreService()
        index_result = vector_service.index_document(
            file_path=current_version['file_path'],
            document_id=document_id,
            version_id=current_version['id'],
            space_id=doc['space_id'],
            document_name=doc['name']
        )

        # 记录审计
        user_id = st.session_state["user"]["id"]
        AuditService.record_document_operation(
            user_id=user_id,
            action_type='document_reindex',
            document_id=document_id,
            version_id=current_version['id'],
            space_id=doc['space_id'],
            is_success=index_result['success'],
            error_message=index_result.get('message') if not index_result['success'] else None
        )

        if index_result['success']:
            st.success(f"重新索引成功：{index_result['message']}")
            time.sleep(1)
            st.rerun()
        else:
            st.error(f"重新索引失败：{index_result['message']}")


def show_deactivate_document(document_id: int):
    """下架文档"""
    result = DocumentService.deactivate_document(document_id)

    # 记录审计
    user_id = st.session_state["user"]["id"]
    detail = DocumentService.get_document_detail(document_id)
    space_id = detail['document']['space_id'] if detail['success'] else None
    version_id = detail['document'].get('current_version_id') if detail['success'] else None

    AuditService.record_document_operation(
        user_id=user_id,
        action_type='document_deactivate',
        document_id=document_id,
        version_id=version_id,
        space_id=space_id,
        is_success=result['success'],
        error_message=result.get('message') if not result['success'] else None
    )

    if result['success']:
        st.success(result['message'])
        time.sleep(1)
        st.rerun()
    else:
        st.error(result['message'])


def show_restore_document(document_id: int):
    """恢复文档"""
    result = DocumentService.restore_document(document_id)

    # 记录审计
    user_id = st.session_state["user"]["id"]
    detail = DocumentService.get_document_detail(document_id)
    space_id = detail['document']['space_id'] if detail['success'] else None
    version_id = result.get('version_id')

    AuditService.record_document_operation(
        user_id=user_id,
        action_type='document_restore',
        document_id=document_id,
        version_id=version_id,
        space_id=space_id,
        is_success=result['success'],
        error_message=result.get('message') if not result['success'] else None
    )

    if result['success']:
        st.success(result['message'])
        time.sleep(1)
        st.rerun()
    else:
        st.error(result['message'])
