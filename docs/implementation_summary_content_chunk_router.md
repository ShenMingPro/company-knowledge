# 基于内容结构的分块策略路由器实施总结

## 实施日期
2026-09-12

## 实施目标
实现基于内容结构的分块策略路由器，根据文档内容特征智能选择合适的分块策略，而不是根据文件扩展名决定分块方式。

## 一、原来的分块方式

### 问题描述
在实施前，所有文件类型统一使用 `RecursiveCharacterTextSplitter`：

```python
# rag/vector_store.py (旧版本)
def _parse_document(self, filepath):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=self.chunk_size,
        chunk_overlap=self.chunk_overlap,
        separators=["\n\n", "\n", "。", "!", "?", "；", ";", "，", ",", " ", ""]
    )
    return loader(filepath, text_splitter)
```

**局限性**：
1. 不考虑内容结构，可能把问题和答案拆开
2. 章节标题可能与内容分离
3. 表格内容可能被随意切分
4. 步骤说明可能不完整

## 二、新的分块方式

### 核心理念
**文件类型负责选择解析器，内容结构负责选择分块策略。**

### 处理流程
```
文件 
→ 根据扩展名选择解析器 (TXT/PDF/DOCX)
→ 获取文本、页码和结构 metadata
→ 检测内容结构 (qa/section/steps/paragraph/recursive)
→ 选择分块策略
→ 生成最终分块
→ 进入向量入库流程
```

### 模块设计
新增 `rag/chunk_strategy.py`，提供统一接口：

```python
ChunkStrategyRouter.split(documents, source_type, config) -> list[Document]
```

**路由器包含**：
1. `ContentStructureDetector`: 内容结构检测
2. `QAChunker`: 问答分块器
3. `SectionChunker`: 章节分块器
4. `StepsChunker`: 步骤分块器
5. `ParagraphChunker`: 段落分块器
6. `RecursiveChunker`: 递归兜底分块器

## 三、内容结构类型

### 1. 问答结构 (qa)
**识别标记**：
- `问题：` / `答案：`
- `Q:` / `A:`
- `FAQ`、`常见问题`

**分块规则**：
- 一个问题和对应答案放在同一分块
- 问答对过长时递归切分
- metadata 包含 `chunk_strategy: "question_answer"`

### 2. 章节结构 (section)
**识别标记**：
- Markdown 标题: `#`、`##`、`###`
- 数字编号: `1.`、`1.1`、`1.1.1`
- 中文章节: `第一章`、`一、`、`（一）`

**分块规则**：
- 标题开始新章节
- 标题后的内容归属该章节
- 章节过长时在章节内部递归切分
- metadata 包含 `section_title` 和 `chunk_strategy: "section"`

### 3. 步骤结构 (steps)
**识别标记**：
- 连续数字: `1.`、`2.`、`3.`
- 括号数字: `(1)`、`(2)`、`(3)`
- 列表符号: `-`、`•`
- 关键词: `步骤`、`注意事项`

**分块规则**：
- 保持完整步骤或列表项
- 多个短步骤可合并
- 单个步骤过长时递归切分
- metadata 包含 `chunk_strategy: "steps"`

### 4. 段落结构 (paragraph)
**适用场景**：
- 存在多个自然段
- 没有明显问答、章节或步骤结构

**分块规则**：
- 优先按段落边界合并
- 超过 chunk_size 后递归切分
- metadata 包含 `chunk_strategy: "paragraph"`

### 5. 递归兜底 (recursive)
**适用场景**：
- 无明显结构
- 结构判断不确定
- 结构化策略处理失败

**分块规则**：
- 使用 `RecursiveCharacterTextSplitter`
- metadata 包含 `chunk_strategy: "recursive"`

## 四、策略选择优先级

```python
def detect_structure(text: str) -> str:
    """按优先级检测内容结构"""
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

**规则说明**：
1. 明确的问答标记 → qa
2. 明确的章节标题 → section
3. 明确的连续步骤或列表 → steps
4. 多个自然段 → paragraph
5. 无明显结构 → recursive

## 五、不同文件类型的处理

### 1. TXT 文件
- 对完整文本进行结构检测
- 根据检测结果选择策略
- 示例：
  - FAQ 文本 → qa
  - 章节文档 → section
  - 操作步骤 → steps
  - 普通说明 → paragraph

### 2. PDF 文件
- **页面是硬边界**，不跨页合并
- 每页内部单独执行结构检测
- 页内使用章节、步骤、段落或递归分块
- 保留原始 `page` 或 `page_number`

**示例**：
```python
# 第 0 页：问答结构
{"page": 0, "chunk_strategy": "question_answer"}

# 第 1 页：章节结构
{"page": 1, "chunk_strategy": "section"}
```

### 3. DOCX 文件
- 使用已实现的结构化提取结果
- 根据 `block_type` 处理：
  - `heading` → 参与章节识别
  - `paragraph` → 作为段落内容
  - `table` → 保持完整或按行分割

**metadata 继承**：
- `section_path`：章节路径
- `block_type`：内容块类型
- `heading_level`：标题级别
- `table_index`：表格索引

**示例**：
```python
{
    "block_type": "heading",
    "section_path": "产品介绍",
    "chunk_strategy": "section"
}

{
    "block_type": "table",
    "section_path": "产品介绍 > 主要功能",
    "chunk_strategy": "recursive"
}
```

## 六、分块 metadata

### 每个分块保留的字段

**新增字段**：
- `chunk_strategy`: 使用的分块策略
- `section_title`: 章节标题（section 策略）
- `original_structure`: 原始内容结构

**继承字段**：
- `source`: 文件路径
- `page` / `page_number`: 页码（PDF）
- `section_path`: 章节路径（DOCX）
- `block_type`: 内容块类型（DOCX）

**系统补充字段**（由 vector_store.py 添加）：
- `document_id`: 文档ID
- `document_version_id`: 版本ID
- `space_id`: 空间ID
- `source_name`: 文件名
- `chunk_index`: 分块索引
- `is_active`: 是否激活

## 七、与现有系统的集成

### 修改的文件
1. **rag/chunk_strategy.py**（新增）
   - `ChunkStrategyRouter`: 统一路由接口
   - `ContentStructureDetector`: 结构检测器
   - 5 个具体分块器

2. **rag/vector_store.py**（修改）
   ```python
   # 旧版本
   docs = text_splitter.split_documents(raw_docs)
   
   # 新版本
   docs = ChunkStrategyRouter.split(
       raw_docs, 
       source_type=ext,
       config={
           'chunk_size': self.chunk_size,
           'chunk_overlap': self.chunk_overlap,
           'separators': ["\n\n", "\n", "。", "!", "?"]
       }
   )
   ```

### 保持不变的部分
- 文档解析接口
- 向量入库流程
- 检索和引用逻辑
- 权限和版本管理
- 审计和反馈功能
- 数据库表结构

## 八、测试结果

### 单元测试 (test_chunk_router.py)
```bash
$ python tests/test_chunk_router.py
============================================================
内容结构分块策略路由器测试
============================================================

=== 测试内容结构检测 ===
✓ FAQ 问答结构识别正确
✓ 章节结构识别正确
✓ 步骤列表结构识别正确
✓ 段落结构识别正确
✓ 无明显结构识别正确

=== 测试问答分块 ===
✓ 问答分块生成 2 个分块

=== 测试章节分块 ===
✓ 章节分块生成 3 个分块

=== 测试步骤分块 ===
✓ 步骤分块生成 1 个分块

=== 测试段落分块 ===
✓ 段落分块生成 2 个分块

=== 测试递归兜底 ===
✓ 递归兜底生成 1 个分块

=== 测试超长内容递归切分 ===
✓ 超长内容切分为 4 个分块

=== 测试同一文件中不同结构 ===
✓ 不同结构生成 2 个分块

=== 测试 metadata 保留 ===
✓ metadata 保留测试通过

=== 测试 chunk_index 连续性 ===
✓ chunk_index 连续

============================================================
测试完成: 10 通过, 0 失败
============================================================
```

### 集成测试 (test_chunk_router_integration.py)
```bash
$ python tests/test_chunk_router_integration.py
============================================================
内容结构分块路由器集成测试
============================================================

=== 测试 PDF 分块不跨页 ===
✓ 生成 2 个分块，页码: [0, 1]

=== 测试 TXT 文件结构检测 ===
✓ TXT 问答文件使用策略: {'question_answer'}
✓ TXT 章节文件使用策略: {'section'}

=== 测试 DOCX 结构化分块 ===
✓ DOCX 生成 5 个分块
✓ 标题块数量: 2
✓ 表格块数量: 1
✓ 章节路径保留正确

=== 测试 metadata 兼容性 ===
✓ 原有 metadata 完整保留
✓ 新增 chunk_strategy 字段

=== 测试超长内容递归切分 ===
✓ 超长文本切分为 5 个分块

=== 测试表格内容保留 ===
✓ 表格内容完整保留

============================================================
集成测试完成: 6 通过, 0 失败
============================================================
```

### 回归测试
- ✅ DOCX 提取测试：11 通过
- ✅ 可信 RAG 测试：13 通过，1 个已存在的失败（与本次无关）
- ✅ 引用分组测试：8 通过

## 九、已知限制

### 1. 结构检测限制
- 基于规则，不使用大模型
- 可能误判复杂混合结构
- 不识别表格内部的语义关系

### 2. 分块边界限制
- PDF 严格按页边界，可能切断跨页章节
- 超长章节递归切分可能失去部分上下文
- 表格按行切分可能影响理解

### 3. metadata 限制
- `section_path` 仅对 DOCX 有完整支持
- TXT 和 PDF 的章节路径需要临时构建
- 不支持多级嵌套表格的结构保留

### 4. 性能限制
- 每个文档都要进行结构检测
- 正则表达式可能影响大文件处理速度
- 没有缓存机制

## 十、下一步改进方向

### 1. 大模型辅助结构判断
使用轻量级模型辅助复杂结构判断：
```python
# 示例：不确定的情况下调用模型
if confidence < 0.7:
    structure = llm.detect_structure(text[:500])
```

### 2. 父子分块
保留完整章节作为父分块，子句作为子分块：
```python
parent_chunk = {
    "content": "第一章 产品介绍\n完整内容...",
    "children": [child1, child2, child3]
}
```

### 3. 表格专用处理
- 识别表格结构（表头、数据行）
- 保留列关系
- 支持表格语义检索

### 4. 向量+BM25 混合检索
- 结构化内容使用 BM25
- 语义内容使用向量
- RRF 融合排序

### 5. 动态 chunk_size
根据内容类型自适应调整：
- 问答：较小 chunk_size
- 章节：较大 chunk_size
- 表格：按表格大小决定

## 十一、实施声明

**本次实现的是基于规则的内容结构分块策略路由器。**

特点：
- ✅ 根据内容特征智能选择分块策略
- ✅ 支持问答、章节、步骤、段落和递归五种策略
- ✅ 与现有系统无缝集成
- ✅ 完整的单元测试和集成测试
- ✅ 向后兼容现有 metadata

限制：
- ❌ 不使用大模型判断结构
- ❌ 未实现父子分块
- ❌ 未实现表格专用向量检索
- ❌ 未实现 BM25/RRF 混合检索
- ❌ 没有声称检索准确率已经提升

**这是内容结构感知的第一步，为后续更高级的分块策略提供了基础。**
