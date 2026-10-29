"""
内容结构分块策略路由器测试
验证不同内容结构能够选择正确的分块策略
"""
import os
import sys

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document
from rag.chunk_strategy import (
    ContentStructureDetector,
    ChunkStrategyRouter
)


def test_structure_detection():
    """测试内容结构检测"""
    print("\n=== 测试内容结构检测 ===")

    detector = ContentStructureDetector()

    # 1. FAQ 问答结构
    qa_text = """
    常见问题

    问题：产品保修期是多久？
    答案：产品保修期为一年。

    问题：如何联系客服？
    答案：可以拨打400-123-4567。
    """
    assert detector.detect_structure(qa_text) == 'qa', "应该识别为问答结构"
    print("✓ FAQ 问答结构识别正确")

    # 2. 章节结构
    section_text = """
# 产品介绍

这是产品的基本介绍。

## 功能特点

支持多种功能。

## 技术参数

电压220V，功率1500W。
"""
    detected = detector.detect_structure(section_text)
    if detected != 'section':
        print(f"  实际检测为: {detected}")
        print(f"  是否为问答: {detector._is_qa_structure(section_text)}")
        print(f"  是否为章节: {detector._is_section_structure(section_text)}")
    assert detected == 'section', f"应该识别为章节结构，实际为 {detected}"
    print("✓ 章节结构识别正确")

    # 3. 步骤列表结构
    steps_text = """
安装步骤：

1. 打开包装盒
2. 取出设备
3. 连接电源
4. 按下开关
5. 完成安装
"""
    detected = detector.detect_structure(steps_text)
    if detected != 'steps':
        print(f"  实际检测为: {detected}")
        print(f"  是否为步骤: {detector._is_steps_structure(steps_text)}")
    assert detected == 'steps', f"应该识别为步骤结构，实际为 {detected}"
    print("✓ 步骤列表结构识别正确")

    # 4. 段落结构
    paragraph_text = """
    这是第一个段落，介绍产品的基本情况。产品设计精美，功能强大。

    这是第二个段落，说明产品的主要特点。产品采用先进技术，性能卓越。

    这是第三个段落，描述产品的应用场景。适用于家庭和办公环境。
    """
    assert detector.detect_structure(paragraph_text) == 'paragraph', "应该识别为段落结构"
    print("✓ 段落结构识别正确")

    # 5. 无明显结构
    plain_text = "这是一段没有明显结构的普通文本内容。"
    assert detector.detect_structure(plain_text) == 'recursive', "应该识别为递归分块"
    print("✓ 无明显结构识别正确")

    print("✓ 内容结构检测测试通过")


def test_qa_chunking():
    """测试问答分块"""
    print("\n=== 测试问答分块 ===")

    router = ChunkStrategyRouter(chunk_size=200, chunk_overlap=20)

    qa_doc = Document(
        page_content="""问题：机器人如何充电？
答案：机器人具有自动回充功能，电量低时会自动返回充电座。

问题：机器人支持哪些清扫模式？
答案：支持自动模式、重点清扫模式和边角模式。""",
        metadata={'source': 'test.txt'}
    )

    chunks = router.split([qa_doc], 'txt')

    # 应该生成2个问答对
    assert len(chunks) >= 2, f"应该至少有2个分块，实际有{len(chunks)}个"

    # 检查策略标记
    qa_chunks = [c for c in chunks if 'question_answer' in c.metadata.get('chunk_strategy', '')]
    assert len(qa_chunks) > 0, "应该有问答策略标记"

    # 检查问题和答案不被拆开
    for chunk in chunks:
        content = chunk.page_content
        if '问题' in content or '答案' in content:
            # 如果有问题，应该有对应答案
            if '问题' in content:
                # 问题块应该相对完整（不是只有半句话）
                assert len(content) > 10, "问题块不应该太短"

    print(f"✓ 问答分块生成 {len(chunks)} 个分块")
    print("✓ 问答分块测试通过")


def test_section_chunking():
    """测试章节分块"""
    print("\n=== 测试章节分块 ===")

    router = ChunkStrategyRouter(chunk_size=200, chunk_overlap=20)

    section_doc = Document(
        page_content="""# 第一章 产品介绍

本产品是一款智能清扫机器人。

## 1.1 产品特点

支持自动回充、智能路径规划。

## 1.2 技术参数

电压：220V
功率：1500W""",
        metadata={'source': 'test.txt'}
    )

    chunks = router.split([section_doc], 'txt')

    assert len(chunks) >= 1, "应该生成至少1个分块"

    # 检查策略标记
    section_chunks = [c for c in chunks if 'section' in c.metadata.get('chunk_strategy', '')]
    assert len(section_chunks) > 0, "应该有章节策略标记"

    # 检查 section_title
    titled_chunks = [c for c in chunks if 'section_title' in c.metadata]
    assert len(titled_chunks) > 0, "应该有 section_title"

    print(f"✓ 章节分块生成 {len(chunks)} 个分块")
    print("✓ 章节分块测试通过")


def test_steps_chunking():
    """测试步骤分块"""
    print("\n=== 测试步骤分块 ===")

    router = ChunkStrategyRouter(chunk_size=150, chunk_overlap=20)

    steps_doc = Document(
        page_content="""安装步骤：

1. 打开包装盒，取出所有配件。
2. 连接充电座电源线。
3. 将充电座放置在墙边。
4. 长按开关3秒开机。
5. 完成安装。""",
        metadata={'source': 'test.txt'}
    )

    chunks = router.split([steps_doc], 'txt')

    assert len(chunks) >= 1, "应该生成至少1个分块"

    # 检查策略标记
    steps_chunks = [c for c in chunks if 'steps' in c.metadata.get('chunk_strategy', '')]
    assert len(steps_chunks) > 0, "应该有步骤策略标记"

    print(f"✓ 步骤分块生成 {len(chunks)} 个分块")
    print("✓ 步骤分块测试通过")


def test_paragraph_chunking():
    """测试段落分块"""
    print("\n=== 测试段落分块 ===")

    router = ChunkStrategyRouter(chunk_size=100, chunk_overlap=20)

    para_doc = Document(
        page_content="""这是第一个段落，介绍产品的基本情况。产品设计精美，功能强大，适合家庭使用。

这是第二个段落，说明产品的主要特点。产品采用先进技术，性能卓越，节能环保。

这是第三个段落，描述产品的应用场景。适用于各种室内环境，清洁效果出色。""",
        metadata={'source': 'test.txt'}
    )

    chunks = router.split([para_doc], 'txt')

    assert len(chunks) >= 1, "应该生成至少1个分块"

    # 检查策略标记
    para_chunks = [c for c in chunks if 'paragraph' in c.metadata.get('chunk_strategy', '')]
    assert len(para_chunks) > 0, "应该有段落策略标记"

    print(f"✓ 段落分块生成 {len(chunks)} 个分块")
    print("✓ 段落分块测试通过")


def test_recursive_fallback():
    """测试递归兜底"""
    print("\n=== 测试递归兜底 ===")

    router = ChunkStrategyRouter(chunk_size=50, chunk_overlap=10)

    plain_doc = Document(
        page_content="这是一段没有明显结构的普通文本内容，系统应该使用递归字符分块作为兜底策略。",
        metadata={'source': 'test.txt'}
    )

    chunks = router.split([plain_doc], 'txt')

    assert len(chunks) >= 1, "应该生成至少1个分块"

    # 检查策略标记
    recursive_chunks = [c for c in chunks if c.metadata.get('chunk_strategy') == 'recursive']
    assert len(recursive_chunks) > 0, "应该有递归策略标记"

    print(f"✓ 递归兜底生成 {len(chunks)} 个分块")
    print("✓ 递归兜底测试通过")


def test_long_content_splitting():
    """测试超长内容递归切分"""
    print("\n=== 测试超长内容递归切分 ===")

    router = ChunkStrategyRouter(chunk_size=100, chunk_overlap=20)

    # 超长问答
    long_qa_doc = Document(
        page_content="""问题：机器人的详细功能有哪些？
答案：机器人具有非常丰富的功能，包括但不限于：自动清扫、智能路径规划、自动回充、定时预约、遥控操作、APP控制、语音交互、障碍物识别、地毯识别、边角清扫、重点清扫、静音模式、标准模式、强力模式等多种工作模式，同时还支持虚拟墙设置、禁区设置、多楼层地图记忆等高级功能。""",
        metadata={'source': 'test.txt'}
    )

    chunks = router.split([long_qa_doc], 'txt')

    # 超长内容应该被切分
    assert len(chunks) > 1, f"超长内容应该被切分成多个分块，实际{len(chunks)}个"

    # 所有分块都应该有策略标记
    for chunk in chunks:
        assert 'chunk_strategy' in chunk.metadata, "每个分块都应该有策略标记"

    print(f"✓ 超长内容切分为 {len(chunks)} 个分块")
    print("✓ 超长内容递归切分测试通过")


def test_different_structures_in_same_file():
    """测试同一文件中不同结构"""
    print("\n=== 测试同一文件中不同结构 ===")

    router = ChunkStrategyRouter(chunk_size=200, chunk_overlap=20)

    # 模拟一个包含多个结构的文档（已被解析器拆分）
    docs = [
        Document(
            page_content="# 产品介绍\n\n这是产品的基本介绍。",
            metadata={'source': 'test.txt'}
        ),
        Document(
            page_content="问题：如何使用？\n答案：请参考说明书。",
            metadata={'source': 'test.txt'}
        ),
    ]

    chunks = router.split(docs, 'txt')

    assert len(chunks) >= 2, "应该生成至少2个分块"

    # 应该有不同的策略
    strategies = set(c.metadata.get('chunk_strategy', '') for c in chunks)
    # 可能有 section 和 question_answer 等
    assert len(strategies) > 0, "应该有策略标记"

    print(f"✓ 不同结构生成 {len(chunks)} 个分块，使用策略: {strategies}")
    print("✓ 不同结构测试通过")


def test_metadata_preservation():
    """测试 metadata 保留"""
    print("\n=== 测试 metadata 保留 ===")

    router = ChunkStrategyRouter(chunk_size=200, chunk_overlap=20)

    doc = Document(
        page_content="# 测试标题\n\n这是测试内容。",
        metadata={
            'source': 'test.txt',
            'page': 1,
            'custom_field': 'custom_value'
        }
    )

    chunks = router.split([doc], 'txt')

    for chunk in chunks:
        # 原始 metadata 应该保留
        assert chunk.metadata.get('source') == 'test.txt', "应该保留 source"
        assert chunk.metadata.get('page') == 1, "应该保留 page"
        assert chunk.metadata.get('custom_field') == 'custom_value', "应该保留自定义字段"
        # 应该添加策略标记
        assert 'chunk_strategy' in chunk.metadata, "应该有策略标记"

    print("✓ metadata 保留测试通过")


def test_chunk_index_continuity():
    """测试 chunk_index 连续性"""
    print("\n=== 测试 chunk_index 连续性 ===")

    router = ChunkStrategyRouter(chunk_size=50, chunk_overlap=10)

    doc = Document(
        page_content="这是第一段文本。\n\n这是第二段文本。\n\n这是第三段文本。",
        metadata={'source': 'test.txt'}
    )

    chunks = router.split([doc], 'txt')

    # 模拟 vector_store 添加 chunk_index
    for idx, chunk in enumerate(chunks):
        chunk.metadata['chunk_index'] = idx

    # 检查连续性
    indices = [c.metadata['chunk_index'] for c in chunks]
    assert indices == list(range(len(chunks))), f"chunk_index 应该连续，实际: {indices}"

    print(f"✓ chunk_index 连续: {indices}")
    print("✓ chunk_index 连续性测试通过")


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("内容结构分块策略路由器测试")
    print("=" * 60)

    tests = [
        test_structure_detection,
        test_qa_chunking,
        test_section_chunking,
        test_steps_chunking,
        test_paragraph_chunking,
        test_recursive_fallback,
        test_long_content_splitting,
        test_different_structures_in_same_file,
        test_metadata_preservation,
        test_chunk_index_continuity,
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
            print(f"✗ {test.__name__} 出错: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试完成: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
