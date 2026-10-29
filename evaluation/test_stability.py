"""
生成稳定性验证脚本

测试同一问题连续生成 N 次，比较关键要点覆盖的稳定性
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.rag_service import RagSummarizeService
from model.factory import create_chat_model
from utils.config_handler import rag_conf
import json


def extract_key_facts(answer: str) -> set:
    """
    从答案中提取关键事实（简化版，实际应使用更复杂的提取逻辑）
    """
    facts = set()

    # 提取数字 + 单位
    import re

    # 提取天数
    days = re.findall(r'(\d+)\s*天', answer)
    facts.update([f"{d}天" for d in days])

    # 提取关键词
    keywords = ['书面通知', 'OA', '离职申请', '文档移交', '权限回收',
                '客户资源', '离职面谈', '工资结算', '年假', '离职证明']
    for kw in keywords:
        if kw in answer:
            facts.add(kw)

    return facts


def run_generation_stability(question: str, n_runs: int = 5):
    """
    测试生成稳定性：同一问题连续生成 N 次

    :param question: 测试问题
    :param n_runs: 运行次数
    """
    print("=" * 80)
    print(f"生成稳定性测试")
    print("=" * 80)
    print(f"问题: {question}")
    print(f"运行次数: {n_runs}")
    print(f"模型配置: temperature={rag_conf.get('chat_model_temperature', 'N/A')}, "
          f"top_p={rag_conf.get('chat_model_top_p', 'N/A')}")
    print("-" * 80)

    # 创建 RAG 服务（使用 temperature=0 的模型）
    rag = RagSummarizeService(user_role='employee', use_hybrid=True)

    answers = []
    facts_list = []

    for i in range(n_runs):
        print(f"\n第 {i+1} 次生成...")
        result = rag.rag_summarize(query=question, history=[])

        answer = result.get("answer", "")
        has_answer = result.get("has_answer", False)

        if not has_answer:
            print(f"  ❌ 拒答: {result.get('refusal_reason', 'N/A')}")
            answers.append(None)
            facts_list.append(set())
            continue

        # 提取关键事实
        facts = extract_key_facts(answer)
        answers.append(answer)
        facts_list.append(facts)

        print(f"  ✓ 回答长度: {len(answer)} 字符")
        print(f"  ✓ 关键事实: {facts}")

    # 分析稳定性
    print("\n" + "=" * 80)
    print("稳定性分析")
    print("=" * 80)

    # 有效回答数量
    valid_answers = [a for a in answers if a is not None]
    print(f"有效回答: {len(valid_answers)}/{n_runs}")

    if len(valid_answers) < 2:
        print("⚠️  有效回答不足 2 个，无法分析稳定性")
        return

    # 关键事实交集和并集
    valid_facts = [f for f in facts_list if f]
    facts_intersection = set.intersection(*valid_facts) if valid_facts else set()
    facts_union = set.union(*valid_facts) if valid_facts else set()

    print(f"\n关键事实稳定性:")
    print(f"  所有回答共有: {facts_intersection}")
    print(f"  至少一次出现: {facts_union}")
    print(f"  稳定率: {len(facts_intersection)}/{len(facts_union)} = "
          f"{len(facts_intersection)/len(facts_union)*100:.1f}%" if facts_union else "N/A")

    # 逐次对比
    print(f"\n逐次对比:")
    for i in range(len(valid_facts)):
        for j in range(i+1, len(valid_facts)):
            common = valid_facts[i] & valid_facts[j]
            total = valid_facts[i] | valid_facts[j]
            similarity = len(common) / len(total) if total else 0
            print(f"  第{i+1}次 vs 第{j+1}次: {len(common)}/{len(total)} = {similarity*100:.1f}% 相似")

    # 答案文本相似度（简化版：完全相同比较）
    print(f"\n文本级别稳定性:")
    unique_answers = set(valid_answers)
    print(f"  唯一答案数: {len(unique_answers)}")
    if len(unique_answers) == 1:
        print("  ✅ 所有回答完全相同（文本级别）")
    else:
        print("  ⚠️  回答存在文本差异（预期行为，temperature=0 不保证字节级相同）")

    # 验收标准
    print(f"\n验收标准检查:")
    stability_rate = len(facts_intersection) / len(facts_union) if facts_union else 0
    if stability_rate >= 0.8:  # 80% 稳定率
        print(f"  ✅ 关键事实稳定率 >= 80%")
    else:
        print(f"  ❌ 关键事实稳定率 < 80%，建议检查配置或提示词")

    # 保存详细结果
    result_file = os.path.join(os.path.dirname(__file__), "stability_test_result.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({
            "question": question,
            "n_runs": n_runs,
            "model_config": {
                "temperature": rag_conf.get('chat_model_temperature'),
                "top_p": rag_conf.get('chat_model_top_p')
            },
            "answers": answers,
            "facts_list": [list(f) for f in facts_list],
            "facts_intersection": list(facts_intersection),
            "facts_union": list(facts_union),
            "stability_rate": stability_rate
        }, f, ensure_ascii=False, indent=2)

    print(f"\n详细结果已保存到: {result_file}")


def run_judge_stability(question: str, answer: str, n_runs: int = 5):
    """
    测试裁判稳定性：同一答案连续裁判 N 次

    :param question: 问题
    :param answer: 答案
    :param n_runs: 运行次数
    """
    print("\n" + "=" * 80)
    print(f"裁判稳定性测试")
    print("=" * 80)
    print(f"问题: {question}")
    print(f"答案: {answer[:100]}...")
    print(f"运行次数: {n_runs}")
    print(f"模型配置: temperature={rag_conf.get('judge_model_temperature', 'N/A')}, "
          f"top_p={rag_conf.get('judge_model_top_p', 'N/A')}")
    print("-" * 80)

    # 加载 judge 模型
    judge_model = create_chat_model(purpose="judge")

    # 加载 golden_set
    golden_set_path = os.path.join(os.path.dirname(__file__), "golden_set.json")
    with open(golden_set_path, "r", encoding="utf-8") as f:
        golden_set = json.load(f)

    # 找到对应题目
    item = None
    for q in golden_set:
        if q["question"] == question:
            item = q
            break

    if not item or not item.get("key_points"):
        print("⚠️  题目没有 key_points，无法测试")
        return

    # 构造评测项
    test_item = {
        "id": item["id"],
        "question": question,
        "key_points": item["key_points"],
        "system_answer": answer
    }

    # 导入 judge_one
    from evaluation.run_eval_v2 import judge_one

    verdicts = []
    scores = []
    coverage_rates = []

    for i in range(n_runs):
        print(f"\n第 {i+1} 次裁判...")
        result = judge_one(test_item, judge_model)

        verdict = result.get("verdict")
        score = result.get("score")
        coverage_stats = result.get("coverage_stats", {})
        coverage_rate = coverage_stats.get("coverage_rate", 0.0)

        verdicts.append(verdict)
        scores.append(score)
        coverage_rates.append(coverage_rate)

        print(f"  verdict: {verdict}, score: {score}, coverage: {coverage_rate*100:.1f}%")

    # 分析稳定性
    print("\n" + "=" * 80)
    print("裁判稳定性分析")
    print("=" * 80)

    unique_verdicts = set(verdicts)
    unique_scores = set(scores)

    print(f"verdict 稳定性: {len(unique_verdicts)} 种结果 - {unique_verdicts}")
    print(f"score 稳定性: {len(unique_scores)} 种结果 - {unique_scores}")
    print(f"coverage 范围: {min(coverage_rates)*100:.1f}% ~ {max(coverage_rates)*100:.1f}%")

    # 验收标准
    print(f"\n验收标准检查:")
    if len(unique_verdicts) == 1:
        print(f"  ✅ verdict 完全一致（{verdicts[0]}）")
    else:
        print(f"  ❌ verdict 不一致，需要检查裁判逻辑或模型配置")

    if len(unique_scores) == 1:
        print(f"  ✅ score 完全一致（{scores[0]}）")
    else:
        print(f"  ❌ score 不一致，需要检查判分规则")


if __name__ == "__main__":
    # 测试 1：生成稳定性（emp_06）
    run_generation_stability(
        question="想离职的话需要提前多久提？忘记了怎么办？",
        n_runs=5
    )

    # 测试 2：裁判稳定性（使用部分覆盖的答案）
    partial_answer = (
        "正式员工离职需提前30天书面通知，试用期员工提前3天通知。"
        "需要在OA系统提交离职申请。"
    )
    run_judge_stability(
        question="想离职的话需要提前多久提？忘记了怎么办？",
        answer=partial_answer,
        n_runs=5
    )
