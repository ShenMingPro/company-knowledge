# 结构化分块系统完整实施总结

## 实施日期
2026-09-12

## 总体目标
实现从"文件类型决定分块"到"内容结构决定分块"的智能分块系统，包括：
1. DOCX 结构化文本提取
2. 基于内容结构的分块策略路由器

## 实施概览

本次实施分为两个阶段：

### 阶段一：DOCX 结构化文本提取
**目标**：让程序能够保留 DOCX 文档的段落、标题、表格及其顺序

**核心改进**：
- 从 `Docx2txtLoader`（单一文本块）→ `python-docx`（结构化内容块）
- 识别标题样式和文本规则标题
- 保留内容原始顺序
- 维护章节路径

### 阶段二：内容结构分块策略路由器
**目标**：根据文档内容特征智能选择合适的分块策略

**核心理念**：
- 文件类型负责选择解析器
- 内容结构负责选择分块策略

## 一、架构变化

### 旧架构
```
文件 → 根据扩展名选择加载器 → RecursiveCharacterTextSplitter → 向量库
```

**问题**：
- 所有文件统一分块，不考虑内容结构
- 问题和答案可能被拆开
- 章节标题可能与内容分离
- 表格内容可能被随意切分

### 新架构
```
文件 
→ 根据扩展名选择解析器 (TXT/PDF/DOCX)
→ 获取文本、页码和结构 metadata
→ 内容结构检测 (ContentStructureDetector)
→ 分块策略路由 (ChunkStrategyRouter)
→ 智能分块 (QA/Section/Steps/Paragraph/Recursive)
→ 向量库
```

**优势**：
- 问答结构：问题和答案保持在同一分块
- 章节结构：标题和内容归属清晰
- 步骤结构：完整步骤不被切断
- 表格结构：表格内容完整保留

## 二、DOCX 结构化提取详情

### 实施文件
- `utils/file_handler.py`: 修改 `docx_loader()` 函数

### 核心功能

#### 1. 内容块类型
支持三种结构化内容块：

**标题块**：
```python
{
    "page_content": "第一章 产品介绍",
    "metadata": {
        "block_type": "heading",
        "heading_level": 1,
        "is_heading": True,
        "style_name": "Heading 1",
        "section_path": "第一章 产品介绍",
        "source": "manual.docx"
    }
}
```

**段落块**：
```python
{
    "page_content": "本产品适用于家庭场景...",
    "metadata": {
        "block_type": "paragraph",
        "paragraph_index": 0,
        "is_heading": False,
        "section_path": "第一章 产品介绍",
        "source": "manual.docx"
    }
}
```

**表格块**：
```python
{
    "page_content": "字段 | 内容\n型号 | KB-R2\n故障码 | E03",
    "metadata": {
        "block_type": "table",
        "table_index": 0,
        "row_count": 3,
        "column_count": 2,
        "section_path": "第一章 产品介绍",
        "source": "manual.docx"
    }
}
```

#### 2. 标题识别规则
**优先级 1：Word 样式**
- `Heading 1`, `Heading 2`, `Heading 3` 等
- `标题 1`, `标题 2`, `标题 3` 等

**优先级 2：文本规则**
- Markdown: `## 使用说明`
- 数字编号: `1. 产品介绍`, `1.1 使用方法`
- 中文章节: `第一章 产品介绍`, `一、使用说明`

**保守原则**：
- 普通正文不会因为长度较短而被误判为标题

#### 3. 章节路径维护
```
第一章 产品介绍
    → section_path: "第一章 产品介绍"
    
    段落：本产品适用于家庭场景。
        → section_path: "第一章 产品介绍"
    
    1.1 产品特点
        → section_path: "第一章 产品介绍 > 1.1 产品特点"
        
        段落：支持自动回充。
            → section_path: "第一章 产品介绍 > 1.1 产品特点"
```

#### 4. 内容顺序保证
使用 `python-docx` 的底层 body 子节点遍历，确保：
- 标题、段落、表格的原始顺序不变
- 不会先遍历所有段落再遍历所有表格

### 测试覆盖 (test_docx_extraction.py)
- ✅ 标题、段落、表格的提取
- ✅ 内容原始顺序保持
- ✅ Word 样式标题识别
- ✅ 文本规则标题识别
- ✅ 普通正文不会被误判
- ✅ 章节路径继承
- ✅ 表格内容保留
- ✅ 型号和故障码完整性
- ✅ 空段落不生成内容块
- ✅ 空白 DOCX 处理
- ✅ metadata 与现有系统兼容

**测试结果**：11 通过 / 0 失败

## 三、内容结构分块路由器详情

### 实施文件
- `rag/chunk_strategy.py`: 新增分块路由模块
- `rag/vector_store.py`: 修改 `index_document()` 使用路由器

### 核心组件

#### 1. ChunkStrategyRouter (路由器)
统一入口：
```python
ChunkStrategyRouter.split(
    documents,      # 解析后的文档列表
    source_type,    # 文件类型 (txt/pdf/docx)
    config          # 分块配置
) -> list[Document]
```

#### 2. ContentStructureDetector (结构检测器)
检测内容结构类型：
```python
def detect_structure(text: str) -> str:
    """返回 qa/section/steps/paragraph/recursive"""
    if _is_qa_structure(text):
        return "qa"
    if _is_section_structure(text):
        return "section"
    if _is_steps_structure(text):
        return "steps"
    if _is_paragraph_structure(text):
        return "paragraph"
    return "recursive"
```

#### 3. 五种分块策略

**QAChunker (问答分块器)**
- 识别：`问题：`/`答案：`, `Q:`/`A:`, `FAQ`
- 规则：问题和答案放在同一分块
- metadata: `chunk_strategy: "question_answer"`

**SectionChunker (章节分块器)**
- 识别：`#` 标题, `1.` 编号, `第一章`, `一、`
- 规则：标题开始新章节，内容归属该章节
- metadata: `chunk_strategy: "section"`, `section_title`

**StepsChunker (步骤分块器)**
- 识别：`1.`/`2.`/`3.`, `(1)`/`(2)`, `-`/`•`
- 规则：保持完整步骤，多个短步骤可合并
- metadata: `chunk_strategy: "steps"`

**ParagraphChunker (段落分块器)**
- 识别：多个自然段，无明显其他结构
- 规则：优先按段落边界合并
- metadata: `chunk_strategy: "paragraph"`

**RecursiveChunker (递归兜底)**
- 识别：无明显结构
- 规则：使用 `RecursiveCharacterTextSplitter`
- metadata: `chunk_strategy: "recursive"`

### 不同文件类型的处理

#### TXT 文件
```python
# 对完整文本进行结构检测
structure = detect_structure(text)
# 根据结构选择分块器
chunker = get_chunker(structure)
chunks = chunker.split(text)
```

**示例**：
- FAQ 文本 → `question_answer` 策略
- 章节文档 → `section` 策略
- 操作步骤 → `steps` 策略

#### PDF 文件
```python
# 页面是硬边界，不跨页合并
for page in pages:
    structure = detect_structure(page.content)
    chunks = get_chunker(structure).split(page)
    # 保留原始页码
    for chunk in chunks:
        chunk.metadata['page'] = page.metadata['page']
```

**重要约束**：
- 页面是硬边界
- 每页内部单独检测结构
- 不同页可以使用不同策略

#### DOCX 文件
```python
# 使用已提取的结构化内容块
heading_blocks = [b for b in blocks if b.metadata['block_type'] == 'heading']
paragraph_blocks = [b for b in blocks if b.metadata['block_type'] == 'paragraph']
table_blocks = [b for b in blocks if b.metadata['block_type'] == 'table']

# 标题参与章节识别
# 段落作为内容
# 表格保持完整或按行分割
```

**metadata 继承**：
- `section_path`: 从 DOCX 提取继承
- `block_type`: heading/paragraph/table
- `chunk_strategy`: 路由器添加

### 测试覆盖

#### 单元测试 (test_chunk_router.py)
- ✅ 内容结构检测（5种结构）
- ✅ 问答分块
- ✅ 章节分块
- ✅ 步骤分块
- ✅ 段落分块
- ✅ 递归兜底
- ✅ 超长内容递归切分
- ✅ 同一文件不同结构
- ✅ metadata 保留
- ✅ chunk_index 连续性

**测试结果**：10 通过 / 0 失败

#### 集成测试 (test_chunk_router_integration.py)
- ✅ PDF 分块不跨页
- ✅ TXT 文件结构检测
- ✅ DOCX 结构化分块
- ✅ metadata 兼容性
- ✅ 超长内容递归切分
- ✅ 表格内容保留

**测试结果**：6 通过 / 0 失败

#### 回归测试
- ✅ DOCX 提取测试：11 通过
- ✅ 可信 RAG 测试：13 通过，1 个已存在的失败
- ✅ 引用分组测试：8 通过

## 四、metadata 演变

### 原有 metadata
```python
{
    "source": "manual.pdf",
    "page": 0,
    "document_id": "doc_123",
    "document_version_id": "ver_456",
    "space_id": "space_789",
    "source_name": "manual.pdf",
    "chunk_index": 0,
    "is_active": True
}
```

### DOCX 新增 metadata
```python
{
    # ... 原有字段 ...
    "block_type": "heading",           # 内容块类型
    "heading_level": 1,                # 标题级别
    "is_heading": True,                # 是否标题
    "section_path": "第一章 > 1.1",    # 章节路径
    "style_name": "Heading 1",         # Word 样式
    "table_index": 0,                  # 表格索引
    "row_count": 3,                    # 表格行数
    "column_count": 2                  # 表格列数
}
```

### 路由器新增 metadata
```python
{
    # ... 原有字段 + DOCX 字段 ...
    "chunk_strategy": "section",       # 使用的分块策略
    "section_title": "产品介绍",       # 章节标题
    "original_structure": "section"    # 原始内容结构
}
```

**兼容性保证**：
- 新字段不覆盖原有字段
- 原有字段继续由 vector_store.py 补充
- 引用逻辑可以读取 section_path 和 section_title

## 五、核心代码变化

### 1. utils/file_handler.py
```python
# 旧版本：使用 Docx2txtLoader
def docx_loader(filepath):
    loader = Docx2txtLoader(filepath)
    return loader.load()

# 新版本：使用 python-docx，返回结构化内容块
def docx_loader(filepath):
    from docx import Document as DocxDocument
    
    doc = DocxDocument(filepath)
    documents = []
    section_stack = []
    
    # 按原始顺序遍历所有元素
    for element in doc.element.body:
        if element.tag.endswith('p'):  # 段落
            # 识别标题或段落
        elif element.tag.endswith('tbl'):  # 表格
            # 提取表格内容
    
    return documents
```

### 2. rag/chunk_strategy.py (新增)
```python
class ChunkStrategyRouter:
    @staticmethod
    def split(documents, source_type, config):
        """统一分块入口"""
        detector = ContentStructureDetector()
        chunks = []
        
        for doc in documents:
            # 检测内容结构
            structure = detector.detect(doc.page_content)
            
            # 选择分块器
            chunker = _get_chunker(structure, config)
            
            # 执行分块
            doc_chunks = chunker.split(doc)
            chunks.extend(doc_chunks)
        
        # 统一编号
        for i, chunk in enumerate(chunks):
            chunk.metadata['chunk_index'] = i
        
        return chunks
```

### 3. rag/vector_store.py
```python
# 旧版本：统一使用 RecursiveCharacterTextSplitter
def _parse_document(self, filepath):
    text_splitter = RecursiveCharacterTextSplitter(...)
    return loader(filepath, text_splitter)

# 新版本：使用 ChunkStrategyRouter
def _parse_document(self, filepath):
    # 第一步：根据扩展名选择解析器
    raw_docs = loader(filepath)  # 获取结构化内容
    
    # 第二步：使用路由器智能分块
    from rag.chunk_strategy import ChunkStrategyRouter
    docs = ChunkStrategyRouter.split(
        raw_docs,
        source_type=ext,
        config={
            'chunk_size': self.chunk_size,
            'chunk_overlap': self.chunk_overlap,
            'separators': ["\n\n", "\n", "。"]
        }
    )
    
    return docs
```

## 六、关键设计决策

### 1. 为什么不用大模型判断结构？
**理由**：
- 成本考虑：每个文档都调用模型成本高
- 延迟考虑：结构检测需要快速响应
- 可控性：规则清晰，便于调试和优化
- 准确性：简单规则对常见结构足够准确

**未来可选**：
- 不确定的情况下调用轻量级模型
- 使用本地小模型辅助判断

### 2. 为什么 PDF 不跨页合并？
**理由**：
- 页面是自然边界
- 跨页合并可能破坏逻辑完整性
- 保留页码信息对引用很重要

**已知限制**：
- 章节跨页时可能被切断
- 长表格跨页时可能分离

### 3. 为什么表格不做深度解析？
**理由**：
- 表格结构复杂多样
- 合并单元格难以准确还原
- 表格语义理解需要领域知识

**当前方案**：
- 保留行列文本关系
- 使用稳定的分隔符
- 较短表格保持完整
- 过长表格按行切分

### 4. 为什么不实现父子分块？
**理由**：
- 需要修改向量库存储结构
- 需要修改检索逻辑
- 增加系统复杂度

**当前方案**：
- 先完成基础结构感知
- 为未来父子分块预留 metadata

## 七、性能影响

### 正面影响
1. **分块质量提升**
   - 问答不被拆开
   - 章节归属清晰
   - 步骤保持完整

2. **引用更准确**
   - section_path 提供上下文
   - block_type 说明内容类型
   - chunk_strategy 说明分块方式

3. **检索相关性提升**（理论）
   - 语义完整的分块更容易匹配
   - 结构化 metadata 可用于过滤

### 负面影响
1. **处理时间增加**
   - 结构检测需要正则匹配
   - DOCX 需要遍历所有元素
   - 估计增加 10-20% 处理时间

2. **metadata 增大**
   - 每个分块增加 3-5 个字段
   - 估计增加 5% 存储空间

### 性能优化方向
1. 缓存结构检测结果
2. 并行处理多个文档
3. 使用编译的正则表达式
4. 只在需要时构建 section_path

## 八、已知限制

### 1. 结构检测限制
- ❌ 基于规则，可能误判复杂结构
- ❌ 不识别表格内部语义
- ❌ 不处理图片和公式
- ❌ 混合结构可能选错策略

### 2. 分块边界限制
- ❌ PDF 严格按页，可能切断跨页章节
- ❌ 超长章节递归切分可能失去上下文
- ❌ 表格按行切分可能影响理解

### 3. metadata 限制
- ❌ section_path 仅 DOCX 有完整支持
- ❌ TXT 和 PDF 章节路径临时构建
- ❌ 不支持多级嵌套表格

### 4. 文件类型限制
- ❌ 不支持 PPT/PPTX
- ❌ 不支持扫描件 OCR
- ❌ 不支持图片内容理解
- ❌ 不支持音视频字幕

## 九、下一步改进方向

### 短期（1-2周）
1. **监控和调优**
   - 收集真实文档的结构分布
   - 统计各策略的使用频率
   - 调整结构检测规则

2. **引用增强**
   - 引用中显示 section_path
   - 引用按章节分组
   - 高亮匹配的具体句子

### 中期（1-2月）
1. **大模型辅助**
   - 不确定时调用轻量级模型
   - 使用本地小模型降低成本
   - A/B 测试效果提升

2. **表格专用处理**
   - 识别表头和数据行
   - 保留列关系
   - 支持表格问答

3. **后续已完成能力**
   - 向量检索 + BM25
   - RRF 融合排序
   - Reranker 重排序
   - Query 改写与离线评测

### 长期（3-6月）
1. **父子分块**
   - 完整章节作为父分块
   - 子句作为子分块
   - 检索子块，返回父块上下文

2. **多模态支持**
   - 图片 OCR 和理解
   - PPT 幻灯片解析
   - 音视频字幕提取

3. **GraphRAG**
   - 提取实体和关系
   - 构建知识图谱
   - 图+向量混合检索

## 十、文件清单

### 新增文件
- `rag/chunk_strategy.py`: 分块策略路由器
- `tests/test_docx_extraction.py`: DOCX 提取测试
- `tests/test_docx_integration.py`: DOCX 集成测试
- `tests/test_chunk_router.py`: 分块路由器测试
- `tests/test_chunk_router_integration.py`: 分块路由器集成测试
- `docs/implementation_summary_docx_extraction.md`: DOCX 提取文档
- `docs/implementation_summary_content_chunk_router.md`: 路由器文档
- `docs/implementation_summary_structured_chunking.md`: 本文档

### 修改文件
- `utils/file_handler.py`: DOCX 解析改为结构化
- `rag/vector_store.py`: 使用 ChunkStrategyRouter
- `tests/test_rag_trusted.py`: 修复 mock 返回值

### 保持不变的文件
- `backend/app/routers/documents.py`: 文档管理接口
- `frontend/src/views/DocumentsView.vue`: 文档管理页面
- `services/document_service.py`: 文档服务
- `services/interaction_service.py`: 交互服务
- `rag/rag_service.py`: RAG 服务
- `models/`: 数据库模型
- `prompts/`: 提示词模板

## 十一、测试总结

### 单元测试
- ✅ DOCX 提取：11 通过 / 0 失败
- ✅ 分块路由器：10 通过 / 0 失败

### 集成测试
- ✅ DOCX 集成：11 通过 / 0 失败
- ✅ 分块路由器集成：6 通过 / 0 失败

以上数字为结构化分块实施阶段的专项测试结果；当前全量回归结果以 README 和最新测试运行为准。

## 十二、最终声明

**本次实现完成了智能结构化分块系统的基础版本。**

✅ **已实现**：
- DOCX 结构化文本提取（标题、段落、表格）
- 基于规则的内容结构检测（5种结构）
- 智能分块策略路由（5种策略）
- 完整的单元测试和集成测试
- 向后兼容现有系统
- 详细的实施文档

❌ **未实现**（明确不在本次范围）：
- 大模型辅助结构判断
- 父子分块
- 表格专用向量检索
- GraphRAG
- 多模态支持

后续版本已完成 BM25/RRF 混合检索、Query 改写、Reranker 和离线评测系统。

⚠️ **重要说明**：
- 本次改进提升了分块质量，但没有声称检索准确率已经提升
- 需要在真实场景中监控效果
- 为后续更高级的 RAG 优化提供了基础

---

**实施完成日期**：2026-09-12  
**实施者**：Claude Code  
**文档版本**：1.0
