# KnowBase Lite - 企业私有化知识助手

面向中小型制造企业客服与员工的轻量私有化知识助手，支持三角色权限、文档版本管理、权限感知检索、带来源引用的RAG问答、售后故障排查和操作审计，并兼容云端模型与本地模型部署。

## 功能特点

- **三角色权限体系**：普通员工、客服、管理员，各有不同的访问权限
- **知识空间隔离**：产品知识库、企业制度库、售后知识库、管理员空间
- **文档版本管理**：支持文档更新、版本追溯、无缝切换、版本隔离
- **权限感知检索**：在检索层面实施权限过滤，确保数据安全
- **来源引用展示**：每个答案都带有文档来源和原文片段
- **完整文档生命周期**：上传、索引、版本管理、下架、恢复、软删除
- **操作审计日志**：记录所有关键操作，便于追溯和分析
- **多模型支持**：兼容云端模型（DashScope）和本地模型（Ollama，计划中）

## 系统架构

```
页面层（Streamlit）
    ↓
应用服务层
    ↓
业务服务层（文档服务、权限服务、RAG服务、审计服务）
    ↓
数据层（SQLite、ChromaDB、模型提供商）
```

## 技术栈

- **Web框架**：Streamlit
- **LLM框架**：LangChain
- **向量数据库**：ChromaDB
- **关系数据库**：SQLite
- **大语言模型**：通义千问（DashScope）/ Ollama（计划中）
- **文档处理**：PyPDF、python-docx

## 快速开始

### 1. 环境准备

**Python版本要求**：Python 3.10+

克隆项目：
```bash
cd D:\python项目\AI大模型RAG与智能体开发_Agent项目
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

复制环境变量模板：
```bash
cp .env.example .env
```

编辑 `.env` 文件，配置必要参数：

**云端模式（使用DashScope）**：
```env
MODEL_PROVIDER=dashscope
DASHSCOPE_API_KEY=your_dashscope_api_key_here
```

**本地模式（计划中）**：
```env
MODEL_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
```

### 4. 初始化数据库

首次运行前需要初始化数据库（创建表和演示账号）：
```bash
python -m scripts.init_db
```

这将创建三个演示账号：
- **普通员工**：用户名 `employee`，密码配置在`.env`的`DEMO_EMPLOYEE_PASSWORD`
- **客服**：用户名 `service`，密码配置在`.env`的`DEMO_SERVICE_PASSWORD`  
- **管理员**：用户名 `admin`，密码配置在`.env`的`DEMO_ADMIN_PASSWORD`

### 5. 启动应用

```bash
streamlit run app.py
```

浏览器会自动打开 http://localhost:8501

## 使用指南

### 角色权限说明

| 权限 | 普通员工 | 客服 | 管理员 |
|---|:---:|:---:|:---:|
| 查询产品知识 | ✓ | ✓ | ✓ |
| 查询企业制度 | ✓ | ✓ | ✓ |
| 查询售后资料 | ✗ | ✓ | ✓ |
| 生成客服回复 | ✗ | ✓ | ✓ |
| 上传/管理文档 | ✗ | ✗ | ✓ |
| 查看审计日志 | ✗ | ✗ | ✓ |

### 知识空间

系统包含四个知识空间：

1. **产品知识库**：产品功能、使用方法、常见问题
2. **企业制度库**：企业制度、流程规范、培训资料
3. **售后知识库**：故障排查、维修SOP、保修政策（仅客服和管理员可访问）
4. **管理员空间**：内部管理文档（仅管理员可访问）

### 文档管理

1. 使用管理员账号登录
2. 点击侧边栏的"📁 文档管理"按钮
3. 查看文档列表、版本历史
4. 执行以下操作：
   - **上传文档**：在侧边栏选择知识空间并上传
   - **查看详情**：查看文档的所有版本历史
   - **重新索引**：重新处理失败的文档
   - **下架文档**：临时停用文档（不删除数据）
   - **恢复文档**：重新激活已下架的文档
   - **删除文档**：软删除文档（可恢复）

### 文档版本管理

- 同名文档上传会自动创建新版本
- 文件内容未变化不会创建重复版本
- 每个版本独立存储，不会相互覆盖
- 新版本索引成功后自动替换旧版本
- 新版本索引失败时旧版本继续可用
- 支持查看完整版本历史

### 知识问答

1. 登录后在主界面输入问题
2. 系统根据您的角色权限检索相关文档
3. 返回带来源引用的答案
4. 可对答案进行点赞/点踩反馈

## 项目结构

```
.
├── app.py                      # 主应用入口
├── requirements.txt            # 依赖清单
├── .env.example               # 环境变量模板
├── README.md                  # 项目说明
├── IMPLEMENTATION_SPEC.md     # 实施规格说明
│
├── agent/                     # Agent模块（实验功能）
│   ├── react_agent.py
│   └── tools/
│
├── config/                    # 配置文件
│   ├── chroma.yml
│   ├── rag.yml
│   └── ...
│
├── model/                     # 模型工厂
│   └── factory.py
│
├── rag/                       # RAG服务
│   ├── rag_service.py
│   └── vector_store.py
│
├── services/                  # 业务服务层（待创建）
│   ├── auth_service.py
│   ├── document_service.py
│   ├── permission_service.py
│   └── audit_service.py
│
├── models/                    # 数据模型（待创建）
│   └── database.py
│
├── utils/                     # 工具函数
│   ├── config_handler.py
│   ├── file_handler.py
│   ├── logger_handler.py
│   └── ...
│
├── data/                      # 文档存储目录
├── chroma_db/                 # 向量数据库
└── logs/                      # 日志目录
```

## 开发计划

### ✅ 第一阶段：工程止血
- [x] 移除硬编码API Key
- [x] 创建环境变量配置
- [x] 创建依赖清单
- [x] 创建README文档
- [x] 创建.gitignore

### 🚧 第二阶段：知识库核心闭环
- [ ] 用户登录系统
- [ ] 三角色权限
- [ ] 知识空间
- [ ] 文档上传和索引
- [ ] 权限感知检索
- [ ] 带引用的RAG问答
- [ ] 无答案拒答

### 📋 第三阶段：企业化能力
- [ ] 文档版本管理
- [ ] 审计日志
- [ ] 用户反馈
- [ ] 多轮会话
- [ ] 管理员页面

### 📋 第四阶段：效果和交付
- [ ] 混合检索（BM25+向量）
- [ ] Reranker重排
- [ ] 离线评测
- [ ] Docker部署
- [ ] Ollama本地模型支持

## 常见问题

### Q: 如何获取DashScope API Key？

访问 [阿里云DashScope控制台](https://dashscope.console.aliyun.com/)，注册并创建API Key。

### Q: 支持哪些文档格式？

目前支持：PDF、DOCX、TXT。

### Q: 如何切换到本地模型？

将 `.env` 中的 `MODEL_PROVIDER` 改为 `ollama`，并确保本地Ollama服务正在运行（计划中功能）。

### Q: 数据存储在哪里？

- 业务元数据：SQLite数据库（`data/knowbase.db`）
- 向量数据：ChromaDB（`chroma_db/`目录）
- 原始文档：`data/`目录

### Q: 如何备份数据？

备份以下目录和文件：
- `data/knowbase.db` - 业务数据库
- `chroma_db/` - 向量数据库
- `data/` - 原始文档

## 安全说明

- ✅ API Key从环境变量读取，不出现在代码中
- ✅ 上传文件名经过清理，防止路径注入
- ✅ 权限在检索层面执行，不依赖提示词
- ✅ 密码使用bcrypt加密存储
- ✅ 文档采用软删除，可恢复误删数据
- ✅ 操作审计日志完整记录

## 许可证

本项目仅供学习和内部使用。

## 联系方式

如有问题或建议，请提交Issue。
