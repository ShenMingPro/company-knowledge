"""
可信RAG评测脚本
运行校准问题集，生成评测报告
"""
import sys
import io
import os
import time
from typing import Dict, List

# 设置UTF-8输出

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from rag.rag_service import RagSummarizeService
from tests.calibration_questions import CALIBRATION_QUESTIONS, get_questions_for_role
from utils.logger_handler import logger


class RAGEvaluator:
    """RAG评测器"""

    def __init__(self, role: str = 'employee'):
        self.role = role
        self.rag_service = RagSummarizeService(user_role=role)
        self.results = []

    def evaluate_question(self, question_item: Dict) -> Dict:
        """评测单个问题"""
        question = question_item['question']
        q_id = question_item['id']

        print(f"\n测试 {q_id}: {question}")

        try:
            # 调用RAG
            start_time = time.time()
            result = self.rag_service.rag_summarize(question)
            elapsed_ms = int((time.time() - start_time) * 1000)

            # 分析结果
            answer = result['answer']
            has_answer = result.get('has_answer', True)
            references = result.get('references', [])
            refusal_reason = result.get('refusal_reason')

            # 检查是否符合预期
            should_refuse = question_item.get('should_refuse', False)
            expected_contains = question_item.get('expected_answer_contains', [])

            # 判断正确性
            is_correct = self._check_correctness(
                answer, has_answer, should_refuse, expected_contains
            )

            # 检查引用质量
            reference_quality = self._check_reference_quality(references)

            # 构建评测结果
            eval_result = {
                'id': q_id,
                'question': question,
                'category': question_item['category'],
                'answer': answer,
                'has_answer': has_answer,
                'should_refuse': should_refuse,
                'refusal_reason': refusal_reason,
                'reference_count': len(references),
                'references': references,
                'is_correct': is_correct,
                'reference_quality': reference_quality,
                'elapsed_ms': elapsed_ms,
                'retrieved_count': result.get('retrieved_count', 0),
                'valid_count': result.get('valid_count', 0)
            }

            # 打印结果
            status = "✅ 正确" if is_correct else "❌ 错误"
            print(f"  结果: {status}")
            print(f"  拒答: {'是' if not has_answer else '否'}")
            print(f"  引用: {len(references)}条")
            print(f"  耗时: {elapsed_ms}ms")
            if not is_correct:
                print(f"  问题: {self._get_failure_reason(eval_result)}")

            return eval_result

        except Exception as e:
            logger.error(f"评测失败：{q_id} - {str(e)}", exc_info=True)
            return {
                'id': q_id,
                'question': question,
                'category': question_item['category'],
                'error': str(e),
                'is_correct': False
            }

    def _check_correctness(self, answer: str, has_answer: bool,
                          should_refuse: bool, expected_contains: List[str]) -> bool:
        """检查答案正确性"""
        # 如果应该拒答
        if should_refuse:
            # 检查是否拒答了
            if not has_answer:
                return True
            # 或者答案中包含拒答关键词
            refusal_keywords = ["没有找到", "无法确认", "建议补充", "联系人工"]
            if any(keyword in answer for keyword in refusal_keywords):
                return True
            return False

        # 如果不应该拒答
        if not has_answer:
            return False

        # 检查答案是否包含预期关键词
        if expected_contains:
            return any(keyword in answer for keyword in expected_contains)

        # 没有明确预期，只要有答案就算对
        return True

    def _check_reference_quality(self, references: List[Dict]) -> Dict:
        """检查引用质量"""
        quality = {
            'has_references': len(references) > 0,
            'all_have_source': True,
            'all_have_version': True,
            'all_have_location': True,
            'all_have_score': True,
            'issues': []
        }

        for ref in references:
            if not ref.get('source_name'):
                quality['all_have_source'] = False
                quality['issues'].append('缺少source_name')

            if not ref.get('version_number'):
                quality['all_have_version'] = False
                quality['issues'].append('缺少version_number')

            if not ref.get('section') and not ref.get('page'):
                quality['all_have_location'] = False
                quality['issues'].append('缺少定位信息')

            if 'relevance_score' not in ref:
                quality['all_have_score'] = False
                quality['issues'].append('缺少relevance_score')

        return quality

    def _get_failure_reason(self, eval_result: Dict) -> str:
        """获取失败原因"""
        should_refuse = eval_result['should_refuse']
        has_answer = eval_result['has_answer']

        if should_refuse and has_answer:
            return "应该拒答但给出了答案"
        if not should_refuse and not has_answer:
            return "不应该拒答但拒绝了"
        if not should_refuse and eval_result['reference_count'] == 0:
            return "给出答案但没有引用"

        return "未知原因"

    def evaluate_all(self, questions: List[Dict] = None) -> Dict:
        """评测所有问题"""
        if questions is None:
            # 使用当前角色可访问的问题
            questions = get_questions_for_role(self.role)

        print("=" * 60)
        print(f"可信RAG评测 - 角色: {self.role}")
        print(f"问题数量: {len(questions)}")
        print("=" * 60)

        self.results = []
        for q in questions:
            result = self.evaluate_question(q)
            self.results.append(result)
            time.sleep(0.5)  # 避免API限流

        # 生成报告
        return self.generate_report()

    def generate_report(self) -> Dict:
        """生成评测报告"""
        total = len(self.results)
        if total == 0:
            return {'total': 0, 'correct': 0, 'accuracy': 0.0}

        correct = sum(1 for r in self.results if r.get('is_correct', False))
        accuracy = correct / total

        # 按类别统计
        by_category = {}
        for r in self.results:
            cat = r.get('category', '未知')
            if cat not in by_category:
                by_category[cat] = {'total': 0, 'correct': 0}
            by_category[cat]['total'] += 1
            if r.get('is_correct', False):
                by_category[cat]['correct'] += 1

        # 拒答统计
        refusal_stats = {
            'should_refuse_count': sum(1 for r in self.results if r.get('should_refuse', False)),
            'actually_refused_count': sum(1 for r in self.results if not r.get('has_answer', True)),
            'correct_refusal': sum(1 for r in self.results
                                  if r.get('should_refuse', False) and not r.get('has_answer', True)),
            'wrong_refusal': sum(1 for r in self.results
                                if not r.get('should_refuse', False) and not r.get('has_answer', True)),
        }

        # 引用统计
        reference_stats = {
            'total_references': sum(r.get('reference_count', 0) for r in self.results),
            'avg_references': sum(r.get('reference_count', 0) for r in self.results) / total,
            'with_references': sum(1 for r in self.results if r.get('reference_count', 0) > 0),
        }

        # 性能统计
        performance_stats = {
            'avg_time_ms': sum(r.get('elapsed_ms', 0) for r in self.results) / total,
            'max_time_ms': max((r.get('elapsed_ms', 0) for r in self.results), default=0),
            'min_time_ms': min((r.get('elapsed_ms', 0) for r in self.results), default=0),
        }

        report = {
            'role': self.role,
            'total': total,
            'correct': correct,
            'accuracy': accuracy,
            'by_category': by_category,
            'refusal_stats': refusal_stats,
            'reference_stats': reference_stats,
            'performance_stats': performance_stats,
            'results': self.results
        }

        self.print_report(report)
        return report

    def print_report(self, report: Dict):
        """打印报告"""
        print("\n" + "=" * 60)
        print("评测报告")
        print("=" * 60)

        print(f"\n总体准确率: {report['accuracy']:.1%} ({report['correct']}/{report['total']})")

        print("\n按类别统计:")
        for cat, stats in report['by_category'].items():
            acc = stats['correct'] / stats['total'] if stats['total'] > 0 else 0
            print(f"  {cat}: {acc:.1%} ({stats['correct']}/{stats['total']})")

        print("\n拒答统计:")
        rs = report['refusal_stats']
        print(f"  应该拒答: {rs['should_refuse_count']}条")
        print(f"  实际拒答: {rs['actually_refused_count']}条")
        print(f"  正确拒答: {rs['correct_refusal']}条")
        print(f"  错误拒答: {rs['wrong_refusal']}条")

        print("\n引用统计:")
        refs = report['reference_stats']
        print(f"  总引用数: {refs['total_references']}条")
        print(f"  平均引用: {refs['avg_references']:.1f}条/问题")
        print(f"  有引用的: {refs['with_references']}/{report['total']}")

        print("\n性能统计:")
        perf = report['performance_stats']
        print(f"  平均耗时: {perf['avg_time_ms']:.0f}ms")
        print(f"  最大耗时: {perf['max_time_ms']:.0f}ms")
        print(f"  最小耗时: {perf['min_time_ms']:.0f}ms")

        print("\n失败案例:")
        failures = [r for r in report['results'] if not r.get('is_correct', False)]
        if failures:
            for f in failures:
                print(f"  ❌ {f['id']}: {f['question']}")
                print(f"     {self._get_failure_reason(f)}")
        else:
            print("  无失败案例")

        print("=" * 60)


def run_evaluation(role: str = 'employee'):
    """运行评测"""
    evaluator = RAGEvaluator(role=role)
    report = evaluator.evaluate_all()
    return report


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='可信RAG评测')
    parser.add_argument('--role', type=str, default='employee',
                       choices=['employee', 'service', 'admin'],
                       help='测试角色')
    args = parser.parse_args()

    run_evaluation(role=args.role)
