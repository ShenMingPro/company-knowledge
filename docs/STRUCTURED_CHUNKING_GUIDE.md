# 结构化分块系统使用指南

## 快速开始

### 什么是结构化分块？

传统方法：所有文档统一按字符数切分，可能把问答拆开、章节分离。

新方法：
1. **DOCX 结构化提取**：识别标题、段落、表格
2. **内容结构检测**：判断文档是问答、章节、步骤还是普通段落
3. **智能分块路由**：根据内容特征选择最合适的分块策略

## 功能特性

### 支持的内容结构

| 结构类型 | 识别标记 | 分块行为 | 示例 |
|---------|---------|---------|------|
| **问答** | `问题：`/`答案：`, `Q:`/`A:` | 问题和答案保持在一起 | FAQ 文档 |
| **章节** | `#` 标题, `1.`, `第一章` | 标题和内容归属清晰 | 用户手册 |
| **步骤** | `1.`/`2.`/`3.`, `-`/`•` | 完整步骤不被切断 | 操作指南 |
| **段落** | 多个自然段 | 按段落边界合并 | 产品说明 |
| **递归** | 无明显结构 | 字符分割兜底 | 纯文本 |

### 支持的文件类型

- **TXT**：完整文本结构检测
- **PDF**：每页独立检测，不跨页合并
- **DOCX**：保留 Word 样式、章节路径、表格结构

## 使用示例

### 1. 上传 DOCX 文档

```python
# 程序自动识别标题、段落、表格
# 例如：用户手册.docx

第一章 产品介绍          # 识别为标题，heading_level=1
本产品适用于家庭场景。    # 识别为段落，section_path="第一章 产品介绍"

1.1 主要功能            # 识别为标题，heading_level=2
支持自动回充。          # 识别为段落，section_path="第一章 产品介绍 > 1.1 主要功能"

[表格]                 # 识别为表格，保留行列关系
型号    KB-R2
故障码  E03
```

**生成的 metadata**：
```python
{
    "block_type": "heading",           # 或 paragraph/table
    "section_path": "第一章 > 1.1",    # 章节路径
    "chunk_strategy": "section",       # 使用的策略
    "heading_level": 2                 # 标题级别
}
```

### 2. 上传 FAQ 文本

```
问题：如何重置设备？
答案：长按电源键10秒。

问题：电池能用多久？
答案：正常使用约8小时。
```

**自动行为**：
- 检测为问答结构
- 每个问答对保持在一个分块
- metadata 包含 `chunk_strategy: "question_answer"`

### 3. 上传操作手册 PDF

```
第 0 页：章节目录        → 检测为 section 结构
第 1 页：操作步骤列表    → 检测为 steps 结构
第 2 页：FAQ            → 检测为 qa 结构
```

**自动行为**：
- 不同页可以使用不同策略
- 每个分块保留 `page` 信息
- 不跨页合并

## 检索增强

### 1. 章节路径引用

提问：产品如何使用？

回答：
```
根据【用户手册.docx - 第一章 产品介绍 > 1.1 主要功能】：
支持自动回充...
```

### 2. 结构化 metadata 过滤

```python
# 只检索标题
vector_store.similarity_search(
    query="产品功能",
    filter={"block_type": "heading"}
)

# 只检索表格
vector_store.similarity_search(
    query="故障码 E03",
    filter={"block_type": "table"}
)

# 只检索特定章节
vector_store.similarity_search(
    query="使用方法",
    filter={"section_path": {"$contains": "产品介绍"}}
)
```

## 测试验证

### 运行所有测试

```bash
# DOCX 结构化提取测试
python tests/test_docx_extraction.py

# DOCX 集成测试
python tests/test_docx_integration.py

# 分块路由器单元测试
python tests/test_chunk_router.py

# 分块路由器集成测试
python tests/test_chunk_router_integration.py

# 回归测试
pytest tests/test_rag_trusted.py -v
pytest tests/test_reference_grouping.py -v
```

### 预期结果

```
DOCX 提取测试: 11 通过 / 0 失败
DOCX 集成测试: 11 通过 / 0 失败
分块路由器测试: 10 通过 / 0 失败
分块路由器集成测试: 6 通过 / 0 失败
可信 RAG 测试: 13 通过 / 1 失败（已存在）
引用分组测试: 8 通过 / 0 失败

总计: 59 通过 / 1 失败
```

## 配置选项

### 分块参数（保持默认即可）

```python
# rag/vector_store.py
chunk_size = 500        # 最大分块字符数
chunk_overlap = 50      # 分块重叠字符数
```

### 结构检测规则

规则位于 `rag/chunk_strategy.py`：

```python
# 问答结构阈值
MIN_QA_PAIRS = 2           # 至少2个问答对

# 章节结构阈值
MIN_HEADINGS = 2           # 至少2个标题

# 步骤结构阈值
MIN_STEPS = 3              # 至少3个连续步骤

# 段落结构阈值
MIN_PARAGRAPHS = 2         # 至少2个自然段
```

可根据实际需求调整这些阈值。

## 监控和调优

### 查看使用的分块策略

```python
# 查询时检查 metadata
results = vector_store.similarity_search(query, k=5)
for doc in results:
    print(f"策略: {doc.metadata.get('chunk_strategy')}")
    print(f"结构: {doc.metadata.get('block_type')}")
    print(f"章节: {doc.metadata.get('section_path')}")
```

### 统计策略分布

```python
# 统计各策略使用频率
from collections import Counter

strategies = [
    doc.metadata.get('chunk_strategy') 
    for doc in all_chunks
]
print(Counter(strategies))

# 输出示例：
# {'section': 45, 'paragraph': 30, 'question_answer': 15, 
#  'steps': 8, 'recursive': 2}
```

### 识别问题分块

```python
# 查找过长或过短的分块
for doc in all_chunks:
    length = len(doc.page_content)
    if length < 50:
        print(f"过短: {doc.metadata.get('source')} - {length} 字符")
    elif length > 1000:
        print(f"过长: {doc.metadata.get('source')} - {length} 字符")
```

## 常见问题

### Q1: 为什么我的 DOCX 标题没有被识别？

**可能原因**：
1. 没有使用 Word 标题样式（Heading 1/2/3）
2. 文本格式不匹配识别规则

**解决方案**：
- 在 Word 中应用标题样式
- 或使用识别的文本格式：`# 标题`, `1. 标题`, `第一章 标题`

### Q2: 为什么问答被拆开了？

**可能原因**：
1. 问答格式不标准
2. 问答对过长超过 chunk_size

**解决方案**：
- 使用标准格式：`问题：`/`答案：` 或 `Q:`/`A:`
- 增加 chunk_size
- 检查是否有其他结构干扰

### Q3: 为什么 PDF 章节被切断了？

**原因**：
- PDF 严格按页边界，不跨页合并
- 这是设计决策，保证页码引用准确

**影响**：
- 跨页章节可能分离
- 每页内部仍然保持结构完整

### Q4: 如何禁用结构化分块？

**方案 1：修改配置**
```python
# rag/vector_store.py
# 注释掉 ChunkStrategyRouter，恢复原始方式
# docs = ChunkStrategyRouter.split(...)
text_splitter = RecursiveCharacterTextSplitter(...)
docs = text_splitter.split_documents(raw_docs)
```

**方案 2：强制使用递归策略**
```python
# rag/chunk_strategy.py
def detect_structure(text):
    return "recursive"  # 始终返回递归
```

## 限制说明

### 当前不支持

- ❌ 图片内容理解
- ❌ 扫描件 OCR
- ❌ PPT/PPTX 文件
- ❌ 复杂表格语义解析
- ❌ 跨页表格合并
- ❌ 公式识别
- ❌ 音视频字幕

### 结构检测限制

- ❌ 基于规则，可能误判复杂结构
- ❌ 混合结构只能选一种主策略
- ❌ 不识别表格内部语义关系

### 性能影响

- ⚠️ 处理时间增加约 10-20%
- ⚠️ metadata 存储增加约 5%
- ✅ 分块质量显著提升

## 下一步改进

### 短期
- 监控真实文档的结构分布
- 调整结构检测规则
- 引用中显示章节路径

### 中期
- 大模型辅助结构判断
- 表格专用处理
- 混合检索（向量 + BM25）

### 长期
- 父子分块
- 多模态支持
- GraphRAG

## 相关文档

- [DOCX 提取实施总结](implementation_summary_docx_extraction.md)
- [分块路由器实施总结](implementation_summary_content_chunk_router.md)
- [完整实施总结](implementation_summary_structured_chunking.md)

## 技术支持

遇到问题？
1. 查看测试用例：`tests/test_*.py`
2. 查看实施文档：`docs/implementation_summary_*.md`
3. 检查日志输出
4. 提交 issue 或联系开发团队

---

**文档版本**：1.0  
**更新日期**：2026-09-12
