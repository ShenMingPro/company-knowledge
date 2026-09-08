"""
测试检索分数分布
"""
import sys
import io
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from rag.rag_service import RagSummarizeService

def test_retrieval_scores():
    """测试检索分数"""
    rag = RagSummarizeService(user_role='employee')

    test_questions = [
        "小户型适合哪些扫地机器人？",
        "扫地机器人的价格是多少？",
        "如何充电和维护扫地机器人？",
        "公司明年会推出什么新产品？",  # 无答案
    ]

    print("=" * 60)
    print("检索分数分布测试")
    print("=" * 60)

    for question in test_questions:
        print(f"\n问题: {question}")

        docs, scores = rag.retriever_docs_with_scores(question, k=5)

        print(f"检索到 {len(docs)} 个文档")

        for i, (doc, distance) in enumerate(zip(docs, scores), 1):
            relevance = rag.normalize_score(distance)
            passed = distance <= rag.RELEVANCE_THRESHOLD

            print(f"  [{i}] {doc.metadata.get('source_name', '未知')}")
            print(f"      距离: {distance:.4f}, 相似度: {relevance:.4f}, 通过: {'✅' if passed else '❌'}")
            print(f"      内容: {doc.page_content[:80]}...")

    print(f"\n当前阈值: {rag.RELEVANCE_THRESHOLD} (距离)")
    print("说明: 距离越小越相似，距离 <= 阈值则通过")

if __name__ == '__main__':
    test_retrieval_scores()
