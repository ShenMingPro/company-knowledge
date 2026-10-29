# DOCX 结构化文本提取实施总结

## 一、实施概述

本次实施改进了 DOCX 文件的解析方式，从原来的扁平化文本提取升级为结构化内容提取，为后续内容结构分块策略路由器提供基础。

**实施日期**: 2026-09-12  
**影响范围**: `utils/file_handler.py` 中的 `docx_loader()` 函数  
**测试覆盖**: 17 个自动化测试（11 个提取测试 + 6 个集成测试）全部通过

## 二、原来的 DOCX 提取方式

### 2.1 旧实现

```python
def docx_loader(filepath: str) -> list[Document]:
    return Docx2txtLoader(filepath).load()
```

### 2.2 存在的问题

1. **结构信息丢失**: 整个 DOCX 被提取为一个扁平的 Document
2. **标题样式未保留**: 无法区分标题和正文
3. **段落边界不明确**: 所有文本混在一起
4. **表格结构丢失**: 表格内容与普通文本混在一起
5. **原始顺序不可追溯**: 标题、段落、表格的原始顺序无法恢复
6. **章节关系缺失**: 无法知道段落属于哪个章节

## 三、新的 DOCX 提取方式

### 3.1 核心改进

使用 `python-docx` 库按原始顺序遍历文档内容，将每个标题、段落、表格解析为独立的 `Document` 对象。

```python
def docx_loader(filepath: str) -> list[Document]:
    """
    Word 文档加载器：解析 .docx 文件，提取结构化内容（标题、段落、表格）
    :param filepath: docx 文件的路径
    :return: Document 列表，每个 Document 代表一个内容块（标题/段落/表格）
    """
    doc = DocxDocument(filepath)
    blocks = []
    section_stack = []  # 维护章节路径
    
    # 按原始顺序遍历文档元素
    for element in doc.element.body:
        if isinstance(element, CT_P):  # 段落节点
            # 识别标题或段落
        elif isinstance(element, CT_Tbl):  # 表格节点
            # 提取表格内容
    
    return blocks
```

### 3.2 内容块类型

#### 3.2.1 标题块 (heading)

**metadata 字段**:
- `block_type`: "heading"
- `heading_level`: 标题级别（1-6）
- `is_heading`: True
- `style_name`: Word 样式名称或识别规则
- `section_path`: 章节路径（如 "第一章 产品介绍 > 1.1 产品特点"）
- `source`: 文件路径

**page_content**: 标题文本

#### 3.2.2 段落块 (paragraph)

**metadata 字段**:
- `block_type`: "paragraph"
- `paragraph_index`: 段落序号
- `is_heading`: False
- `section_path`: 所属章节路径
- `style_name`: Word 样式名称
- `source`: 文件路径

**page_content**: 段落文本

#### 3.2.3 表格块 (table)

**metadata 字段**:
- `block_type`: "table"
- `table_index`: 表格序号
- `row_count`: 行数
- `column_count`: 列数
- `section_path`: 所属章节路径
- `source`: 文件路径

**page_content**: 表格内容（使用 `|` 分隔单元格，保留行列关系）

示例：
```
字段 | 内容
型号 | KB-R2
故障码 | E03
```

## 四、标题识别规则

### 4.1 样式优先识别

**英文样式**:
- `Heading 1`, `Heading 2`, ..., `Heading 6`
- 正则匹配: `r'Heading\s*(\d+)'`

**中文样式**:
- `标题 1`, `标题 2`, ..., `标题 6`
- 正则匹配: `r'标题\s*(\d+)'`

### 4.2 文本规则识别（保守）

只在样式识别失败时使用，避免误判普通正文：

1. **Markdown 风格**: `# 标题`, `## 标题`
   - 正则: `r'^#{1,6}\s+.+'`

2. **数字编号**: `1. 产品介绍`, `1.1 使用方法`
   - 正则: `r'^\d+(\.\d+)*\.?\s+.+'`
   - 长度限制: 2-50 字符（避免误判正文）

3. **中文章节**: `第一章 产品介绍`, `一、使用说明`
   - 正则: `r'^第[一二三四五六七八九十百]+章.+'` 或 `r'^[一二三四五六七八九十]+、.+'`
   - 长度限制: ≤50 字符

### 4.3 误判防护

- 普通正文不会因为长度较短就被误判为标题
- 文本规则识别有严格的长度和格式限制
- 测试验证：普通正文识别率 100%（0 误判）

## 五、章节路径维护

### 5.1 章节栈机制

```python
section_stack = [(level, title), ...]

# 示例
[
    (1, "第一章 产品介绍"),
    (2, "1.1 产品特点")
]
```

### 5.2 路径构建

章节路径使用 `>` 分隔，示例：

```
标题: 第一章 产品介绍
  section_path: "第一章 产品介绍"

段落: 本产品适用于家庭场景。
  section_path: "第一章 产品介绍"

标题: 1.1 产品特点
  section_path: "第一章 产品介绍 > 1.1 产品特点"

段落: 支持自动回充。
  section_path: "第一章 产品介绍 > 1.1 产品特点"
```

### 5.3 层级更新规则

- 遇到新标题时，移除所有级别 ≥ 当前级别的标题
- 将新标题加入栈顶
- 后续段落和表格继承当前栈的完整路径

## 六、内容顺序保持

### 6.1 实现方式

使用 `doc.element.body` 按原始 XML 顺序遍历：

```python
for element in doc.element.body:
    if isinstance(element, CT_P):      # 段落节点
        # 处理段落或标题
    elif isinstance(element, CT_Tbl):  # 表格节点
        # 处理表格
```

### 6.2 顺序验证

测试文档结构：
```
1. 标题: 第一章 产品介绍
2. 段落: 本产品适用于...
3. 标题: 1.1 产品特点
4. 段落: 支持自动回充...
5. 表格: 型号表格
6. 标题: 1.2 技术参数
7. 段落: 工作电压...
```

提取顺序完全一致，测试通过。

## 七、文本处理规则

### 7.1 换行符规范化

```python
text = text.replace('\r\n', '\n').replace('\r', '\n')
```

### 7.2 空白规范化

```python
text = ' '.join(text.split())  # 规范化空白，但保留型号中的连字符
```

### 7.3 型号和故障码保护

- `KB-R2`: 连字符保留
- `E03`: 完整保留
- `220V`: 单位保留

测试验证：型号和故障码完整性 100%

### 7.4 表格提取

```python
def _extract_table_text(table: Table) -> str:
    lines = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        lines.append(' | '.join(cells))
    return '\n'.join(lines)
```

**不处理的内容**（本次范围外）：
- 复杂图片
- 文本框
- 批注
- 页眉页脚
- 修订记录
- 合并单元格的完整视觉布局

## 八、与现有系统的集成

### 8.1 调用方式不变

`rag/vector_store.py` 中的调用保持不变：

```python
def _parse_document(self, file_path: str) -> list[Document]:
    if file_path.endswith("docx"):
        return docx_loader(file_path)
    # ...
```

### 8.2 metadata 兼容性

新增的 DOCX metadata 不会覆盖现有字段：

```python
# docx_loader 返回的 metadata
{
    'block_type': 'paragraph',
    'section_path': '第一章 产品介绍',
    'source': '/path/to/file.docx',
    ...
}

# vector_store.py 继续添加
doc.metadata.update({
    'space_id': 1,
    'document_id': 100,
    'document_version_id': 200,
    'source_name': 'test.docx',
    'chunk_index': 0,
    'is_active': True
})

# 最终 metadata 包含所有字段
```

### 8.3 分块流程已接入结构路由

```python
# 1. 解析文档（现在返回多个结构化 Document）
documents = self._parse_document(file_path)

# 2. 按文件类型和内容结构选择策略；递归字符分块作为兜底
source_type = self._get_source_type(file_path)
split_documents = self.chunk_router.split(documents, source_type)

# 3. 添加 metadata
for idx, doc in enumerate(split_documents):
    doc.metadata.update({...})

# 4. 存入向量库
self.vector_store.add_documents(split_documents)
```

### 8.4 其他文件类型不受影响

- PDF: 继续使用 `PyPDFLoader`
- TXT: 继续使用 `TextLoader`
- 集成测试验证通过

## 九、测试覆盖

### 9.1 提取测试 (test_docx_extraction.py)

| 测试项 | 状态 |
|--------|------|
| 标题、段落、表格提取 | ✓ 通过 |
| 内容顺序保持 | ✓ 通过 |
| Word 样式标题识别 | ✓ 通过 |
| 文本规则标题识别 | ✓ 通过 |
| 普通正文不误判 | ✓ 通过 |
| 章节路径继承 | ✓ 通过 |
| 表格内容保留 | ✓ 通过 |
| 型号故障码完整性 | ✓ 通过 |
| 空段落跳过 | ✓ 通过 |
| 空白 DOCX 处理 | ✓ 通过 |
| metadata 兼容性 | ✓ 通过 |

**总计**: 11/11 通过

### 9.2 集成测试 (test_docx_integration.py)

| 测试项 | 状态 |
|--------|------|
| 返回多个结构化 Document | ✓ 通过 |
| 与 TextSplitter 配合 | ✓ 通过 |
| metadata 不会被覆盖 | ✓ 通过 |
| PDF loader 保持不变 | ✓ 通过 |
| TXT loader 保持不变 | ✓ 通过 |
| metadata 字段完整性 | ✓ 通过 |

**总计**: 6/6 通过

### 9.3 测试命令

```bash
# 运行 DOCX 提取测试
python -X utf8 tests/test_docx_extraction.py

# 运行集成测试
python -X utf8 tests/test_docx_integration.py

# 运行所有测试（不包括需要数据库的测试）
python -X utf8 -m pytest tests/test_docx_extraction.py tests/test_docx_integration.py -v
```

### 9.4 真实测试结果

```
============================================================
DOCX 结构化文本提取测试
============================================================
测试完成: 11 通过, 0 失败
============================================================

============================================================
DOCX 结构化提取集成测试
============================================================
测试完成: 6 通过, 0 失败
============================================================
```

## 十、当前已知限制

### 10.1 不支持的内容类型

1. **图片**: 图片内容不会被提取（需要 OCR）
2. **文本框**: 文本框中的内容不会被提取
3. **页眉页脚**: 页眉页脚不在 body 中，未处理
4. **批注和修订**: 批注和修订记录未处理
5. **嵌入对象**: 如嵌入的 Excel 表格、PDF 等

### 10.2 表格限制

1. **合并单元格**: 不恢复完整的视觉布局
2. **嵌套表格**: 未特殊处理嵌套表格
3. **表格样式**: 表格边框、背景色等样式信息未保留

### 10.3 标题识别限制

1. **自定义样式**: 只识别标准的 Heading/标题 样式
2. **文本规则**: 文本规则较保守，可能漏掉一些非标准格式的标题
3. **多级列表**: 多级列表的层级关系未完整识别

### 10.4 其他限制

1. **损坏文件**: 损坏的 DOCX 会抛出异常（由上层处理）
2. **超大文件**: 超大文件可能导致内存压力（未做分页处理）
3. **加密文件**: 加密的 DOCX 需要密码解密（未实现）

## 十一、后续演进状态

### 11.1 内容结构分块策略路由器（已完成）

`rag/chunk_strategy.py` 已实现 `ChunkStrategyRouter`，会根据文件类型、`block_type` 和内容结构选择分块策略，失败时回退到递归字符分块。详见 `docs/implementation_summary_structured_chunking.md`。

### 11.2 标题感知分块（已完成）

标题块会独立保留，正文和表格继承 `section_path`，用于结构化分块和后续引用定位。

### 11.3 表格专用检索

```python
class TableRetriever:
    """表格专用检索器"""
    
    def retrieve(self, query: str, k: int = 5):
        """只检索 block_type='table' 的分块"""
        filter_dict = {
            "$and": [
                {"block_type": {"$eq": "table"}},
                {"is_active": {"$eq": True}}
            ]
        }
        # ...
```

### 11.4 章节路径引用

在引用时显示章节路径而不是 chunk_index：

```python
# 原来
"参考：产品手册.docx [分块 3]"

# 改进后
"参考：产品手册.docx [第一章 产品介绍 > 1.1 产品特点]"
```

## 十二、影响范围总结

### 12.1 修改的文件

1. `utils/file_handler.py`:
   - 重写 `docx_loader()` 函数
   - 新增 `_extract_paragraph_text()`
   - 新增 `_identify_heading()`
   - 新增 `_update_section_stack()`
   - 新增 `_build_section_path()`
   - 新增 `_extract_table_text()`

### 12.2 新增的测试文件

1. `tests/test_docx_extraction.py`: 11 个提取测试
2. `tests/test_docx_integration.py`: 6 个集成测试

### 12.3 不受影响的部分

1. `rag/vector_store.py`: 无需修改
2. `services/document_service.py`: 无需修改
3. `backend/app/routers/documents.py`: 无需修改
4. `frontend/src/views/DocumentsView.vue`: 无需修改
5. 数据库结构: 无需修改
6. PDF 和 TXT 解析: 完全不受影响
7. 登录、权限、审计: 完全不受影响

## 十三、当前状态

1. ✓ **已完成**: DOCX 结构化文本提取
2. ✓ **已完成**: 内容结构分块策略路由器
3. ✓ **已完成**: 标题感知分块和 `section_path` 继承
4. ✗ **未完成**: 大模型语义分块
5. ✗ **未完成**: 父子分块
6. ✗ **未完成**: 表格专用检索
7. ✗ **未完成**: 章节路径引用展示优化

## 十四、总结

本次实施成功地将 DOCX 提取从扁平化升级为结构化，为后续的内容结构分块策略提供了坚实的基础。所有测试（17/17）通过，与现有系统完美集成，不影响其他文件类型的处理。

---

**文档版本**: 1.0  
**最后更新**: 2026-09-12  
**维护者**: Claude Code
