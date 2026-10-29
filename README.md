# KnowBase Lite - 企业私有化知识助手

面向中小型制造企业客服与员工的轻量私有化知识助手，支持三角色权限、文档版本管理、权限感知检索、带来源引用的RAG问答、售后故障排查和操作审计，并兼容云端模型与本地模型部署。

## 功能特点

- **三角色权限体系**：普通员工、客服、管理员，各有不同的访问权限
- **知识空间隔离**：产品知识库、企业制度库、售后知识库、管理员空间
- **文档版本管理**：支持文档更新、版本追溯、无缝切换、版本隔离
- **权限感知检索**：在检索层面实施权限过滤，确保数据安全
- **可信RAG问答**：基于相关性判断，证据不足时拒答，防止幻觉
- **可核验引用**：每个答案带文档来源、版本号、页码/分片、相关度分数
- **完整文档生命周期**：上传、索引、版本管理、下架、恢复、软删除
- **提示词注入防护**：文档内容作为数据处理，不执行其中的指令
- **多模型支持**：兼容云端模型（DashScope）和本地模型（Ollama，计划中）

## 系统架构

```
Vue 3 + TypeScript
        ↓ HTTP / JWT
FastAPI（认证、RBAC、知识空间、问答、文档、审计）
        ↓
业务模块（文档、权限、RAG、会话、审计）
        ↓
MySQL 8 + ChromaDB + Redis + 模型提供商
```

## 技术栈

- **前端**：Vue 3、Vite、TypeScript、Pinia、Vue Router
- **后端**：FastAPI、JWT、接口级 RBAC
- **LLM框架**：LangChain
- **向量数据库**：ChromaDB
- **关系数据库**：MySQL 8（SQLite 仅作为本地兼容模式）
- **缓存与任务状态**：Redis（读缓存、接口限流、JWT 注销、索引任务状态）
- **大语言模型**：通义千问（DashScope）/ Ollama（计划中）
- **文档处理**：PyPDF、python-docx、openpyxl
- **部署**：Docker Compose、Nginx

## 快速开始

### 1. 环境准备

推荐使用 Docker Desktop 启动完整环境。手动本地开发则需要 Python 3.10+、Node.js 20+ 和 MySQL 8。

克隆项目：
```bash
git clone https://github.com/ShenMingPro/company-knowledge.git
cd company-knowledge
```

### 2. Docker 一键启动（推荐）

复制 `.env.example` 为 `.env`，至少修改 MySQL 密码、`JWT_SECRET_KEY` 和模型密钥。生产环境不要使用示例中的默认密码。

如果宿主机的 `3306` 已被本地 MySQL 占用，请将 `.env` 中的 `MYSQL_PORT` 设置为 `3307`。这个端口只影响宿主机访问；Docker 内部服务仍通过 MySQL 标准端口 `3306` 通信。

```bash
docker compose up -d --build
```

首次构建需要下载 Python、Node、Nginx 和 MySQL 镜像，因此耗时会比后续启动更长。启动后可访问：

| 地址 | 用途 |
|---|---|
| `http://localhost:5173` | Vue 3 前端 |
| `http://localhost:8000/docs` | FastAPI Swagger 接口文档 |
| `localhost:${MYSQL_PORT}` | 宿主机连接 Docker MySQL 的端口 |

使用下面的命令确认四个服务都已健康：

```bash
docker compose ps
```

预期会看到 `mysql`、`redis`、`backend`、`frontend` 四个服务。其中后端的健康检查会实际连接 MySQL，Redis 状态会显示在 `/api/health/ready` 中。Redis 故障时 readiness 会返回 `503` 并标记为 `degraded`；缓存仍可回源，但鉴权和受限接口也会返回 `503`，避免安全控制被绕过。

#### Docker 在项目中的作用

Docker Compose 将项目拆分为四个独立但协作的服务：

| 服务 | 职责 | 数据/依赖 |
|---|---|---|
| `frontend` | 使用 Nginx 提供 Vue 3 生产包，并将 `/api` 反向代理到后端 | Vue 构建产物 |
| `backend` | 运行 FastAPI、JWT 认证、RBAC、RAG、文档和审计接口 | Python 与 RAG 依赖 |
| `mysql` | 保存用户、知识空间、文档元数据、会话、反馈和审计日志 | MySQL 8 持久卷 `mysql_data` |
| `redis` | 提供列表缓存、固定窗口限流、JWT 注销记录和索引任务状态 | Redis AOF 持久卷 `redis_data` |

前端等待后端健康后启动，后端等待 MySQL 与 Redis 健康后启动。`data/`、`chroma_db/` 和 `logs/` 挂载到后端容器，因此重建前后端容器不会删除上传文件、向量索引或日志；MySQL 与 Redis 数据分别保存于独立的 Docker 卷中。Redis 宿主机端口仅绑定 `127.0.0.1`，不会直接暴露到局域网。

文档上传与重新索引会返回 `task_id`，可通过 `GET /api/tasks/{task_id}` 查询 `queued`、`processing`、`succeeded` 或 `failed` 状态。任务只允许创建者或管理员查询。当前执行器使用 FastAPI `BackgroundTasks`，适合单机部署，但它不是独立的持久任务队列；进程在任务执行期间被强制终止时，Redis 会保留最后状态，但不会自动重试任务。后续需要多实例和自动重试时可替换为 Celery/RQ worker。

Redis 故障时，缓存会自动回源 MySQL，任务状态会在有界的单进程内存中临时保留。限流和 JWT 撤销校验属于安全控制，会采用 fail-closed 策略返回 `503`，避免故障期间绕过限流或让已注销令牌重新生效。

常用运维命令：

```bash
# 查看服务状态
docker compose ps

# 查看实时日志
docker compose logs -f backend

# 停止服务，但保留数据库和文件数据
docker compose down

# 重新构建并启动
docker compose up -d --build
```

部分 Windows Docker Desktop 版本在中文项目路径下使用 Bake 构建时可能出现编码错误。遇到该问题时，在 `.env` 增加 `COMPOSE_BAKE=false` 后重新执行启动命令。

#### 从 SQLite 迁移已有数据到 MySQL

首次 Docker 启动会自动创建表、四个知识空间和演示账号。若已有 `data/knowbase.db`，可在确认目标 MySQL 数据可以被替换后执行：

```bash
docker compose exec backend python infra/migrations/migrate_sqlite_to_mysql.py --sqlite /app/data/knowbase.db --replace-seed
```

该命令会清空 **Docker MySQL 中已有的业务数据** 并导入 SQLite 数据，但不会修改源 SQLite 文件。它会保留用户、知识空间、文档、文档版本、会话、消息、反馈和审计记录。已经完成迁移的环境不需要重复执行。

### 3. 本地开发

安装后端依赖：

```bash
pip install -r requirements.txt
```

安装前端依赖：

```bash
cd frontend
npm install
cd ..
```

### 4. 配置环境变量

复制环境变量模板：
```bash
cp .env.example .env
```

编辑 `.env` 文件，配置必要参数：

```env
DATABASE_URL=mysql+pymysql://knowbase:password@127.0.0.1:3306/knowbase?charset=utf8mb4
JWT_SECRET_KEY=<使用密码生成器创建至少32字节的随机值>
JWT_ISSUER=knowbase-api
JWT_AUDIENCE=knowbase-web
```

`JWT_SECRET_KEY` 必须使用至少 32 字节的随机值；后端会拒绝缺失、过短或仍为示例值的密钥，Docker Compose 也会在未配置时拒绝启动。

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

不设置 `DATABASE_URL` 时，系统会读取 `DATABASE_PATH` 并使用 SQLite 兼容模式。

### 5. 初始化数据库

首次运行前需要初始化数据库（创建表和演示账号）：
```bash
python -m scripts.init_db
```

这将创建三个演示账号：
- **普通员工**：用户名 `employee`，密码配置在`.env`的`DEMO_EMPLOYEE_PASSWORD`
- **客服**：用户名 `service`，密码配置在`.env`的`DEMO_SERVICE_PASSWORD`  
- **管理员**：用户名 `admin`，密码配置在`.env`的`DEMO_ADMIN_PASSWORD`

### 6. 启动前后端

```bash
uvicorn backend.app.main:app --reload --port 8000
```

另开一个终端：

```bash
cd frontend
npm run dev
```

打开 http://localhost:5173。

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

**可信RAG特性**：
- ✅ **相关性判断**：基于向量距离（L2）自动过滤低相关文档
- ✅ **证据不足拒答**：无检索结果或低于阈值时明确拒答，不猜测
- ✅ **完整引用信息**：包含文件名、版本号、页码/分片、相关度分数
- ✅ **性能可观测**：显示检索耗时、生成耗时、文档过滤数量
- ✅ **提示词防护**：文档中的指令内容不会被执行
- ✅ **混合检索**：向量检索 + BM25 关键词检索 + RRF 融合
- ✅ **Query 改写**：多轮对话中自动补全指代和省略，提高检索效果
- ✅ **Reranker 精排**：DashScope gte-rerank-v2 对候选结果精细排序

**使用方式**：
1. 登录后在主界面输入问题
2. 系统根据您的角色权限检索相关文档（权限过滤+相关性过滤）
3. 多轮对话中，系统会自动补全指代词和省略（如"它"→"KB-R2"）
4. 使用混合检索（向量+BM25+RRF融合）召回相关文档
5. 返回带来源引用的答案，每条引用包含：
   - 文档名和版本号
   - 定位信息（PDF页码、Excel工作表/行号或文本分片编号）
   - 相关度分数（向量距离）
   - BM25 分数和 RRF 分数
   - 检索通道（vector、bm25、hybrid）
   - 内容摘要
6. 查看性能指标：检索耗时、生成耗时、总耗时、Query改写耗时

**拒答示例**：
- 问题超出知识库范围 → "当前知识库中没有找到足够依据，无法确认该问题。建议补充相关文档，或联系人工人员处理。"
- 检索结果相关性过低 → 同上
- 权限不足 → 无检索结果，拒答

**相关性阈值说明**：
- 使用 L2 距离衡量相似度（距离越小越相似）
- 当前阈值：0.8（可在 `rag/rag_service.py` 中调整）
- 典型分布：相关文档 0.3-0.7，无关文档 >1.0

## 测试

### 运行单元测试

```bash
# 可信RAG核心功能测试
python -m pytest tests/test_rag_trusted.py -v

# 混合检索测试
python -m pytest tests/test_hybrid_retrieval.py -v

# Query 改写测试
python -m pytest tests/test_query_rewriter.py -v
python -m pytest tests/test_query_rewriter_integration.py -v

# 文档服务测试
python -m pytest tests/test_document_service.py -v

# 权限测试
python -m pytest tests/test_permissions.py -v

# 文档生命周期测试
python -m pytest tests/test_vector_lifecycle.py -v
```

### 运行RAG评测

```bash
# 初始化知识库（首次运行）
python tests/init_knowledge_base.py

# 运行校准评测
python tests/evaluate_rag.py --role employee

# 测试检索分数分布
python tests/test_retrieval_scores.py
```

**测试覆盖**：
- ✅ 分数读取和语义解释
- ✅ 相关性过滤（高相关通过、低相关拒绝）
- ✅ 拒答机制（无结果、低于阈值）
- ✅ 引用结构（完整字段、去重、定位信息）
- ✅ 权限过滤
- ✅ 提示词注入防护
- ✅ 异常处理（不暴露堆栈）
- ✅ 性能指标记录
- ✅ 混合检索（BM25、RRF融合、降级策略）
- ✅ Query 改写（指代补全、回退策略、集成测试）
- ✅ Reranker 精排（DashScope gte-rerank-v2、降级策略、字段对齐）

## 项目结构

```
.
├── backend/                   # FastAPI 接口、JWT/RBAC、适配器和接口测试
├── frontend/                  # Vue 3 + Vite + TypeScript 前端
├── infra/                     # Dockerfile、Nginx、MySQL DDL 和迁移工具
├── docker-compose.yml         # MySQL、FastAPI、Vue/Nginx 编排
├── config/                    # 配置文件
│   ├── chroma.yml
│   ├── rag.yml
│   └── ...
│
├── model/                     # 模型工厂
│   └── factory.py
│
├── rag/                       # RAG服务
│   ├── rag_service.py        # RAG核心服务
│   ├── vector_store.py       # 向量存储
│   ├── bm25_retriever.py     # BM25关键词检索
│   ├── hybrid_retriever.py   # 混合检索（向量+BM25+RRF）
│   ├── query_rewriter.py     # Query改写模块
│   ├── reranker.py           # Reranker精排模块
│   └── chunk_strategy.py     # 分块策略
│
├── services/                  # 共享业务模块
│   ├── auth_service.py
│   ├── document_service.py
│   ├── permission_service.py
│   └── audit_service.py
│
├── prompts/                   # 提示词模板
│   ├── rag_summarize.txt     # RAG答案生成
│   ├── query_rewrite.txt     # Query改写
│   └── ...
│
├── utils/                     # 工具函数
│   ├── config_handler.py
│   ├── file_handler.py
│   ├── logger_handler.py
│   └── ...
│
├── models/                    # MySQL / SQLite 共享 ORM
├── data/                      # 文档存储目录和本地兼容数据库
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
- [x] 用户登录系统
- [x] 三角色权限
- [x] 知识空间
- [x] 文档上传和索引
- [x] 权限感知检索
- [x] 带引用的RAG问答
- [x] 无答案拒答

### 📋 第三阶段：企业化能力
- [x] 文档版本管理
- [x] 审计日志
- [x] 用户反馈
- [x] 多轮会话
- [x] 管理员页面

### 📋 第四阶段：效果和交付
- [x] 混合检索（BM25+向量+RRF融合）
- [x] Query 改写（多轮对话指代补全）
- [x] Reranker重排
- [x] 离线评测
- [x] FastAPI + Vue 3 + MySQL 迁移
- [x] Docker部署
- [ ] Ollama本地模型支持

## 常见问题

### Q: 如何获取DashScope API Key？

访问 [阿里云DashScope控制台](https://dashscope.console.aliyun.com/)，注册并创建API Key。

### Q: 支持哪些文档格式？

目前支持：PDF、DOCX、TXT、XLSX。Excel 文件按工作表和数据行解析，并保留工作表名称、行号及表头语义用于检索与引用定位。

### Q: 如何配置混合检索和 Query 改写？

在 `config/chroma.yml` 中配置：

```yaml
# 混合检索配置
hybrid_retrieval:
  enabled: true  # 是否启用混合检索
  vector_candidate_k: 10  # 向量检索召回数量
  bm25_candidate_k: 10  # BM25 检索召回数量
  final_top_k: 6  # 最终返回数量
  rrf_constant: 60  # RRF 融合常数

# Query 改写配置
query_rewrite:
  enabled: true  # 是否启用 Query 改写
  history_turns: 3  # 使用最近几轮对话
  max_output_length: 200  # 改写结果最大长度
  assistant_answer_max_length: 500  # 历史回答最大长度
```

### Q: Query 改写如何工作？

在多轮对话中，Query 改写会自动补全指代和省略：

```
用户：KB-R2 支持哪些清扫模式？
助手：KB-R2 支持自动模式、局部清扫模式。
用户：那它支持自动回充吗？  ← 指代不明确
系统改写：KB-R2 是否支持自动回充？  ← 适合检索
```

改写后的问题用于检索，原始问题用于答案生成。

### Q: 如何切换到本地模型？

当前可配置 `MODEL_PROVIDER`，但 Ollama 适配仍属于后续功能；现阶段稳定路径是 DashScope。

### Q: 数据存储在哪里？

- 业务元数据：MySQL 8；未设置 `DATABASE_URL` 时回退到 `data/knowbase.db`
- 向量数据：ChromaDB（`chroma_db/`目录）
- 原始文档：`data/`目录

### Q: 如何备份数据？

备份以下目录和文件：
- MySQL 数据库（使用 `mysqldump` 或托管数据库快照）
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
