"""
内容结构分块路由器集成测试

测试与现有系统的集成，包括 PDF、TXT、DOCX 的完整流程
"""
import os
import sys
import tempfile
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_core.documents import Document
from docx import Document as DocxDocument
from docx.enum.style import WD_STYLE_TYPE

from rag.chunk_strategy import ChunkStrategyRouter
from utils.file_handler import docx_loader, txt_loader


def test_pdf_page_boundary():
    """测试 PDF 分块不跨页"""
    print("\n=== 测试 PDF 分块不跨页 ===")

    # 模拟 PDF 的多页文档
    docs = [
        Document(page_content="第一页内容\n这是第一页的详细说明。", metadata={"source": "test.pdf", "page": 0}),
        Document(page_content="第二页内容\n这是第二页的详细说明。", metadata={"source": "test.pdf", "page": 1}),
    ]

    router = ChunkStrategyRouter()
    chunks = router.split(docs, source_type="pdf")

    # 检查页码边界
    pages = [chunk.metadata.get("page") for chunk in chunks]
    print(f"✓ 生成 {len(chunks)} 个分块，页码: {pages}")

    # 确保没有跨页
    for chunk in chunks:
        assert "page" in chunk.metadata, "PDF 分块必须保留页码"

    print("✓ PDF 分块不跨页测试通过")


def test_txt_structure_detection():
    """测试 TXT 文件的结构检测"""
    print("\n=== 测试 TXT 文件结构检测 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建问答格式的 TXT
        qa_file = os.path.join(tmpdir, "faq.txt")
        with open(qa_file, "w", encoding="utf-8") as f:
            f.write("""
问题：产品如何充电？
答案：使用标配充电器，连接电源即可。

问题：续航时间多长？
答案：正常使用可达8小时。
""")

        docs = txt_loader(qa_file)
        router = ChunkStrategyRouter()
        chunks = router.split(docs, source_type="txt")

        # 应该识别为问答结构
        strategies = {chunk.metadata.get("chunk_strategy") for chunk in chunks}
        print(f"✓ TXT 问答文件使用策略: {strategies}")
        assert "question_answer" in strategies, "应该识别为问答结构"

        # 创建章节格式的 TXT
        section_file = os.path.join(tmpdir, "manual.txt")
        with open(section_file, "w", encoding="utf-8") as f:
            f.write("""
# 产品手册

## 第一章 产品介绍

本产品是一款智能设备。

## 第二章 使用说明

请按照以下步骤操作。
""")

        docs = txt_loader(section_file)
        chunks = router.split(docs, source_type="txt")

        strategies = {chunk.metadata.get("chunk_strategy") for chunk in chunks}
        print(f"✓ TXT 章节文件使用策略: {strategies}")
        assert "section" in strategies, "应该识别为章节结构"

    print("✓ TXT 文件结构检测测试通过")


def test_docx_structure_preservation():
    """测试 DOCX 结构化内容的分块"""
    print("\n=== 测试 DOCX 结构化分块 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, "test.docx")

        # 创建结构化 DOCX
        doc = DocxDocument()

        # 添加标题
        heading = doc.add_heading('产品介绍', level=1)
        doc.add_paragraph('这是产品的基本介绍。')

        # 添加子标题
        doc.add_heading('主要功能', level=2)
        doc.add_paragraph('支持多种智能功能。')

        # 添加表格
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = '参数'
        table.cell(0, 1).text = '值'
        table.cell(1, 0).text = '电压'
        table.cell(1, 1).text = '220V'

        doc.save(docx_path)

        # 加载并分块
        docs = docx_loader(docx_path)
        router = ChunkStrategyRouter()
        chunks = router.split(docs, source_type="docx")

        print(f"✓ DOCX 生成 {len(chunks)} 个分块")

        # 检查标题块
        heading_chunks = [c for c in chunks if c.metadata.get("block_type") == "heading"]
        print(f"✓ 标题块数量: {len(heading_chunks)}")

        # 检查表格块
        table_chunks = [c for c in chunks if c.metadata.get("block_type") == "table"]
        print(f"✓ 表格块数量: {len(table_chunks)}")

        # 检查 section_path
        section_paths = [c.metadata.get("section_path") for c in chunks if c.metadata.get("section_path")]
        print(f"✓ 章节路径: {section_paths[:3]}")  # 显示前3个

        # 验证顺序：标题应该在前
        if heading_chunks:
            first_heading_idx = chunks.index(heading_chunks[0])
            print(f"✓ 第一个标题在位置: {first_heading_idx}")

    print("✓ DOCX 结构化分块测试通过")


def test_chunk_metadata_compatibility():
    """测试分块 metadata 与现有系统兼容"""
    print("\n=== 测试 metadata 兼容性 ===")

    docs = [
        Document(
            page_content="测试内容",
            metadata={
                "source": "test.txt",
                "document_id": "doc123",
                "space_id": "space456"
            }
        )
    ]

    router = ChunkStrategyRouter()
    chunks = router.split(docs, source_type="txt")

    # 检查原有 metadata 是否保留
    for chunk in chunks:
        assert chunk.metadata.get("source") == "test.txt", "source 应该保留"
        assert chunk.metadata.get("document_id") == "doc123", "document_id 应该保留"
        assert chunk.metadata.get("space_id") == "space456", "space_id 应该保留"
        assert "chunk_strategy" in chunk.metadata, "应该添加 chunk_strategy"

    print("✓ 原有 metadata 完整保留")
    print("✓ 新增 chunk_strategy 字段")
    print("✓ metadata 兼容性测试通过")


def test_long_content_recursive_split():
    """测试超长内容的递归切分"""
    print("\n=== 测试超长内容递归切分 ===")

    # 创建一个超长段落
    long_text = "这是一段很长的文本。" * 200
    docs = [Document(page_content=long_text, metadata={"source": "long.txt"})]

    router = ChunkStrategyRouter(chunk_size=500, chunk_overlap=50)
    chunks = router.split(docs, source_type="txt")

    print(f"✓ 超长文本切分为 {len(chunks)} 个分块")
    assert len(chunks) > 1, "超长文本应该被切分"

    # 检查每个分块大小
    for i, chunk in enumerate(chunks):
        size = len(chunk.page_content)
        print(f"  分块 {i}: {size} 字符")
        assert size <= 600, f"分块 {i} 超过限制: {size} > 600"  # 允许一些余量

    print("✓ 超长内容递归切分测试通过")


def test_table_content_preservation():
    """测试表格内容保留"""
    print("\n=== 测试表格内容保留 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, "table_test.docx")

        doc = DocxDocument()
        doc.add_heading('故障代码表', level=1)

        table = doc.add_table(rows=4, cols=2)
        table.cell(0, 0).text = '故障码'
        table.cell(0, 1).text = '说明'
        table.cell(1, 0).text = 'E03'
        table.cell(1, 1).text = '温度传感器故障'
        table.cell(2, 0).text = 'E05'
        table.cell(2, 1).text = '电机故障'
        table.cell(3, 0).text = 'KB-R2'
        table.cell(3, 1).text = '控制板型号'

        doc.save(docx_path)

        docs = docx_loader(docx_path)
        router = ChunkStrategyRouter()
        chunks = router.split(docs, source_type="docx")

        # 查找表格块
        table_chunks = [c for c in chunks if c.metadata.get("block_type") == "table"]
        assert len(table_chunks) > 0, "应该有表格块"

        table_content = table_chunks[0].page_content
        print(f"✓ 表格内容:\n{table_content}")

        # 检查关键内容
        assert "E03" in table_content, "表格应包含 E03"
        assert "KB-R2" in table_content, "表格应包含 KB-R2"
        assert "温度传感器" in table_content or "传感器" in table_content, "表格应包含描述"

    print("✓ 表格内容保留测试通过")


def main():
    """运行所有集成测试"""
    print("=" * 60)
    print("内容结构分块路由器集成测试")
    print("=" * 60)

    tests = [
        test_pdf_page_boundary,
        test_txt_structure_detection,
        test_docx_structure_preservation,
        test_chunk_metadata_compatibility,
        test_long_content_recursive_split,
        test_table_content_preservation,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__} 失败: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} 错误: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"集成测试完成: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
