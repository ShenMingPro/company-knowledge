"""
KnowBase Lite - 企业私有化知识助手
主应用入口
"""
import os
import time
import streamlit as st
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 导入服务
from services.auth_service import AuthService
from services.permission_service import PermissionService
from services.document_service import DocumentService
from rag.vector_store import VectorStoreService
from rag.rag_service import RagSummarizeService
from models.database import get_session, KnowledgeSpace
from utils.config_handler import chroma_conf
from utils.path_tool import get_abs_path
from utils.logger_handler import logger
from pages.document_management import show_document_management_page

# 页面配置
st.set_page_config(
    page_title="KnowBase Lite - 企业知识助手",
    page_icon="📚",
    layout="wide"
)

# ==================== 会话状态初始化 ====================
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "user" not in st.session_state:
    st.session_state["user"] = None
if "messages" not in st.session_state:
    st.session_state["messages"] = []


# ==================== 登录页面 ====================
def show_login_page():
    """显示登录页面"""
    st.title("📚 KnowBase Lite")
    st.subheader("企业私有化知识助手")

    st.markdown("---")

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.markdown("### 登录")

        username = st.text_input("用户名", key="login_username")
        password = st.text_input("密码", type="password", key="login_password")

        if st.button("登录", type="primary", use_container_width=True):
            if not username or not password:
                st.error("请输入用户名和密码")
            else:
                with st.spinner("登录中..."):
                    result = AuthService.login(username, password)

                if result["success"]:
                    st.session_state["logged_in"] = True
                    st.session_state["user"] = result["user"]
                    st.success(f"欢迎，{result['user']['username']}！")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(result["message"])

        st.markdown("---")
        st.info("""
        **演示账号：**
        - 普通员工：employee
        - 客服：service
        - 管理员：admin

        密码见`.env`配置文件
        """)


# ==================== 主应用 ====================
def show_main_app():
    """显示主应用"""
    user = st.session_state["user"]
    role = user["role"]
    role_name = PermissionService.get_role_name(role)

    # 顶部信息栏
    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        st.title("📚 KnowBase Lite")
    with col2:
        st.markdown(f"**当前用户：** {user['username']}")
    with col3:
        st.markdown(f"**角色：** {role_name}")
        if st.button("退出登录", type="secondary"):
            st.session_state["logged_in"] = False
            st.session_state["user"] = None
            st.session_state["messages"] = []
            st.rerun()

    st.divider()

    # 侧边栏
    with st.sidebar:
        show_sidebar(role)

    # 主内容区：根据当前页面显示不同内容
    if "current_page" not in st.session_state:
        st.session_state["current_page"] = "chat"

    if st.session_state["current_page"] == "chat":
        show_chat_interface(role)
    elif st.session_state["current_page"] == "documents":
        if PermissionService.can_manage_documents(role):
            show_document_management_page()
        else:
            st.error("您没有权限访问文档管理页面")


# ==================== 侧边栏 ====================
def show_sidebar(role: str):
    """显示侧边栏"""
    st.header("功能菜单")

    # 显示可访问的知识空间
    accessible_spaces = PermissionService.get_accessible_spaces(role)
    st.markdown("### 可访问知识空间")
    for space in accessible_spaces:
        st.markdown(f"- {space['name']}")

    st.divider()

    # 管理员功能
    if PermissionService.can_manage_documents(role):
        st.markdown("### 管理功能")

        # 页面切换
        if "current_page" not in st.session_state:
            st.session_state["current_page"] = "chat"

        if st.button("💬 对话", use_container_width=True,
                    type="primary" if st.session_state["current_page"] == "chat" else "secondary"):
            st.session_state["current_page"] = "chat"
            st.rerun()

        if st.button("📁 文档管理", use_container_width=True,
                    type="primary" if st.session_state["current_page"] == "documents" else "secondary"):
            st.session_state["current_page"] = "documents"
            st.rerun()

        st.divider()
        show_document_upload()
    else:
        st.info("📖 您可以查询以上知识空间的内容")

    st.divider()

    # 新建会话
    if st.button("🆕 新建会话", use_container_width=True):
        st.session_state["messages"] = []
        st.rerun()


# ==================== 文档上传（管理员） ====================
def show_document_upload():
    """显示文档上传功能（仅管理员）"""

    st.markdown("### 快速上传")

    # 选择知识空间
    session = get_session()
    try:
        spaces = session.query(KnowledgeSpace).filter_by(is_enabled=True).all()
        space_options = {f"{s.name}": s.id for s in spaces}
    finally:
        session.close()

    selected_space_name = st.selectbox(
        "知识空间",
        options=list(space_options.keys()),
        key="upload_space"
    )

    # 文件上传
    uploaded_files = st.file_uploader(
        "上传文档",
        type=chroma_conf["allow_knowledge_file_type"],
        accept_multiple_files=True,
        key="file_uploader"
    )

    if uploaded_files and st.button("📤 上传", type="primary", use_container_width=True):
        space_id = space_options[selected_space_name]
        user_id = st.session_state["user"]["id"]

        results = []

        for uploaded_file in uploaded_files:
            # 清理文件名
            safe_name = DocumentService.sanitize_filename(uploaded_file.name)

            # 保存到临时目录
            temp_dir = "data/temp"
            os.makedirs(temp_dir, exist_ok=True)
            temp_path = os.path.join(temp_dir, f"temp_{safe_name}")

            with open(temp_path, "wb") as f:
                f.write(uploaded_file.getvalue())

            file_size = os.path.getsize(temp_path)

            # 创建文档记录
            doc_result = DocumentService.create_document(
                name=safe_name,
                space_id=space_id,
                user_id=user_id,
                temp_file_path=temp_path,
                file_size=file_size
            )

            if doc_result["success"]:
                # 索引文档
                vector_service = VectorStoreService()
                index_result = vector_service.index_document(
                    file_path=doc_result.get("file_path", temp_path),
                    document_id=doc_result["document_id"],
                    version_id=doc_result["version_id"],
                    space_id=space_id,
                    document_name=safe_name
                )

                results.append({
                    "name": safe_name,
                    "doc_result": doc_result,
                    "index_result": index_result
                })
            else:
                results.append({
                    "name": safe_name,
                    "doc_result": doc_result,
                    "index_result": None
                })

            # 清理临时文件
            try:
                os.remove(temp_path)
            except:
                pass

        # 显示结果
        for item in results:
            name = item["name"]
            doc_result = item["doc_result"]
            index_result = item["index_result"]

            if index_result and index_result["success"]:
                st.success(f"✅ {name}：{index_result['message']}")
            elif doc_result.get("status") == "unchanged":
                st.info(f"ℹ️ {name}：{doc_result['message']}")
            else:
                error_msg = index_result["message"] if index_result else doc_result["message"]
                st.error(f"❌ {name}：{error_msg}")


# ==================== 对话界面 ====================
def show_chat_interface(role: str):
    """显示对话界面"""

    # 显示历史消息
    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.write(message["content"])

            # 如果有引用，显示引用
            if message["role"] == "assistant" and "references" in message and message["references"]:
                with st.expander(f"📎 查看引用来源（{len(message['references'])}条）"):
                    for ref in message["references"]:
                        # 构建标题
                        title_parts = [f"**[{ref.get('reference_id', '?')}] {ref['source_name']}**"]
                        if ref.get('version_number'):
                            title_parts.append(f"（版本 {ref['version_number']}）")
                        st.markdown(" ".join(title_parts))

                        # 显示定位信息
                        location_parts = []
                        if ref.get('section'):
                            location_parts.append(f"📍 {ref['section']}")
                        if ref.get('relevance_score'):
                            location_parts.append(f"🎯 相关度 {ref['relevance_score']:.2%}")
                        if location_parts:
                            st.caption(" · ".join(location_parts))

                        # 显示内容摘要
                        st.text(ref['content'])
                        st.divider()

            # 显示性能信息（仅最新消息）
            if message["role"] == "assistant" and message == st.session_state["messages"][-1]:
                if "retrieval_time_ms" in message:
                    perf_text = f"⏱️ 检索 {message['retrieval_time_ms']}ms · 生成 {message['generation_time_ms']}ms · 总计 {message['total_time_ms']}ms"
                    if message.get('retrieved_count', 0) > 0:
                        perf_text += f" · 检索 {message['retrieved_count']} → 有效 {message.get('valid_count', 0)}"
                    st.caption(perf_text)

    # 用户输入
    prompt = st.chat_input("请输入您的问题...")

    if prompt:
        # 显示用户消息
        st.chat_message("user").write(prompt)
        st.session_state["messages"].append({"role": "user", "content": prompt})

        # 生成回复
        with st.spinner("正在思考..."):
            try:
                # 使用RAG服务
                rag_service = RagSummarizeService(user_role=role)
                result = rag_service.rag_summarize(prompt)

                answer = result["answer"]
                references = result.get("references", [])
                has_answer = result.get("has_answer", True)

                # 显示助手回复
                with st.chat_message("assistant"):
                    st.write(answer)

                    # 显示引用
                    if references:
                        with st.expander(f"📎 查看引用来源（{len(references)}条）"):
                            for ref in references:
                                # 构建标题
                                title_parts = [f"**[{ref.get('reference_id', '?')}] {ref['source_name']}**"]
                                if ref.get('version_number'):
                                    title_parts.append(f"（版本 {ref['version_number']}）")
                                st.markdown(" ".join(title_parts))

                                # 显示定位信息
                                location_parts = []
                                if ref.get('section'):
                                    location_parts.append(f"📍 {ref['section']}")
                                if ref.get('relevance_score'):
                                    location_parts.append(f"🎯 相关度 {ref['relevance_score']:.2%}")
                                if location_parts:
                                    st.caption(" · ".join(location_parts))

                                # 显示内容摘要
                                st.text(ref['content'])
                                st.divider()

                    # 显示性能信息
                    if "retrieval_time_ms" in result:
                        perf_text = f"⏱️ 检索 {result['retrieval_time_ms']}ms · 生成 {result['generation_time_ms']}ms · 总计 {result['total_time_ms']}ms"
                        if result.get('retrieved_count', 0) > 0:
                            perf_text += f" · 检索 {result['retrieved_count']} → 有效 {result.get('valid_count', 0)}"
                        st.caption(perf_text)

                # 保存到会话
                st.session_state["messages"].append({
                    "role": "assistant",
                    "content": answer,
                    "references": references,
                    "has_answer": has_answer,
                    "retrieval_time_ms": result.get("retrieval_time_ms", 0),
                    "generation_time_ms": result.get("generation_time_ms", 0),
                    "total_time_ms": result.get("total_time_ms", 0),
                    "retrieved_count": result.get("retrieved_count", 0),
                    "valid_count": result.get("valid_count", 0)
                })

            except Exception as e:
                logger.error(f"对话生成失败：{str(e)}", exc_info=True)
                error_msg = f"抱歉，系统遇到错误：{str(e)}"
                st.error(error_msg)
                st.session_state["messages"].append({
                    "role": "assistant",
                    "content": error_msg,
                    "references": []
                })

        st.rerun()


# ==================== 主程序入口 ====================
def main():
    """主程序"""
    if not st.session_state["logged_in"]:
        show_login_page()
    else:
        show_main_app()


if __name__ == "__main__":
    main()
