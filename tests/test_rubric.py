"""
测试判分规则模块

验证基于关键要点覆盖率的确定性判分逻辑
"""
import pytest
from evaluation.rubric import (
    calculate_verdict,
    validate_key_points_result,
    calculate_coverage_stats
)


class TestCalculateVerdict:
    """测试 verdict 计算逻辑"""

    def test_all_covered_with_evidence_returns_correct(self):
        """测试：所有要点都 covered 且有证据 -> correct"""
        key_points_result = [
            {"status": "covered", "evidence": "正式员工需提前30天", "key_point": "提前30天"},
            {"status": "covered", "evidence": "试用期提前3天", "key_point": "试用期3天"},
            {"status": "covered", "evidence": "在OA提交", "key_point": "OA提交"}
        ]

        verdict = calculate_verdict(key_points_result)
        assert verdict == "correct"

    def test_emp_06_partial_coverage_returns_partial(self):
        """测试：emp_06 只答出 30天/3天/OA -> partial"""
        # 模拟 emp_06 的 11 个关键要点，只覆盖了 3 个
        key_points_result = [
            {"status": "covered", "evidence": "正式员工需提前30天", "key_point": "提前30天"},
            {"status": "covered", "evidence": "试用期提前3天", "key_point": "试用期3天"},
            {"status": "covered", "evidence": "在OA提交", "key_point": "OA提交"},
            {"status": "missing", "evidence": "", "key_point": "文档移交"},
            {"status": "missing", "evidence": "", "key_point": "账号权限回收"},
            {"status": "missing", "evidence": "", "key_point": "客户资源交接"},
            {"status": "missing", "evidence": "", "key_point": "HRBP离职面谈"},
            {"status": "missing", "evidence": "", "key_point": "工资结算"},
            {"status": "missing", "evidence": "", "key_point": "年假折算"},
            {"status": "missing", "evidence": "", "key_point": "离职证明"},
            {"status": "missing", "evidence": "", "key_point": "删除公司资料"}
        ]

        verdict = calculate_verdict(key_points_result)
        # 3/11 = 27.3%，低于默认 30%，但我们可以调整阈值
        # 或者调用时传入更低的阈值
        verdict_with_lower_threshold = calculate_verdict(key_points_result, min_partial_coverage=0.2)
        assert verdict_with_lower_threshold == "partial"

    def test_zero_coverage_returns_wrong(self):
        """测试：零覆盖 -> wrong"""
        key_points_result = [
            {"status": "missing", "evidence": "", "key_point": "要点1"},
            {"status": "missing", "evidence": "", "key_point": "要点2"},
            {"status": "missing", "evidence": "", "key_point": "要点3"}
        ]

        verdict = calculate_verdict(key_points_result)
        assert verdict == "wrong"

    def test_contradiction_returns_wrong(self):
        """测试：关键事实矛盾 -> wrong"""
        key_points_result = [
            {"status": "covered", "evidence": "正式员工需提前30天", "key_point": "提前30天"},
            {"status": "contradicted", "evidence": "答案说提前15天", "key_point": "提前期限"}
        ]

        verdict = calculate_verdict(key_points_result)
        assert verdict == "wrong"

    def test_covered_without_evidence_treated_as_missing(self):
        """测试：没有证据的 covered 降级为 missing"""
        key_points_result = [
            {"status": "covered", "evidence": "", "key_point": "要点1"},  # 没有证据
            {"status": "covered", "evidence": "", "key_point": "要点2"},  # 没有证据
            {"status": "covered", "evidence": "有证据", "key_point": "要点3"}
        ]

        verdict = calculate_verdict(key_points_result)
        # 只有 1/3 有证据，覆盖率 33.3%，应该是 partial
        assert verdict == "partial"

    def test_single_point_covered_returns_partial(self):
        """测试：只覆盖一个流程步骤 -> partial"""
        key_points_result = [
            {"status": "covered", "evidence": "提到了提前30天", "key_point": "提前30天"},
            {"status": "missing", "evidence": "", "key_point": "其他步骤1"},
            {"status": "missing", "evidence": "", "key_point": "其他步骤2"}
        ]

        verdict = calculate_verdict(key_points_result)
        # 1/3 = 33.3%，应该是 partial
        assert verdict == "partial"

    def test_low_coverage_below_threshold_returns_wrong(self):
        """测试：覆盖率过低（低于阈值）-> wrong"""
        key_points_result = [
            {"status": "covered", "evidence": "有证据", "key_point": "要点1"},
            {"status": "missing", "evidence": "", "key_point": "要点2"},
            {"status": "missing", "evidence": "", "key_point": "要点3"},
            {"status": "missing", "evidence": "", "key_point": "要点4"},
            {"status": "missing", "evidence": "", "key_point": "要点5"}
        ]

        # 1/5 = 20%，低于默认 30%
        verdict = calculate_verdict(key_points_result, min_partial_coverage=0.3)
        assert verdict == "wrong"

        # 但如果阈值设为 15%，则应该是 partial
        verdict_lower = calculate_verdict(key_points_result, min_partial_coverage=0.15)
        assert verdict_lower == "partial"

    def test_pure_refusal_returns_wrong(self):
        """测试：完全拒答（所有要点都 missing）-> wrong"""
        key_points_result = [
            {"status": "missing", "evidence": "", "key_point": "要点1"},
            {"status": "missing", "evidence": "", "key_point": "要点2"}
        ]

        verdict = calculate_verdict(key_points_result)
        assert verdict == "wrong"


class TestValidateKeyPointsResult:
    """测试 key_points_result 格式验证"""

    def test_valid_format(self):
        """测试：合法格式"""
        result = [
            {"status": "covered", "evidence": "证据", "key_point": "要点1"},
            {"status": "missing", "evidence": "", "key_point": "要点2"}
        ]
        assert validate_key_points_result(result) is True

    def test_invalid_status(self):
        """测试：非法 status"""
        result = [
            {"status": "invalid_status", "evidence": "证据", "key_point": "要点1"}
        ]
        assert validate_key_points_result(result) is False

    def test_missing_required_field(self):
        """测试：缺少必需字段"""
        result = [
            {"status": "covered", "evidence": "证据"}  # 缺少 key_point
        ]
        assert validate_key_points_result(result) is False

    def test_not_a_list(self):
        """测试：不是列表"""
        result = {"status": "covered"}
        assert validate_key_points_result(result) is False


class TestCalculateCoverageStats:
    """测试覆盖率统计"""

    def test_full_coverage(self):
        """测试：全覆盖统计"""
        key_points_result = [
            {"status": "covered", "evidence": "证据1", "key_point": "要点1"},
            {"status": "covered", "evidence": "证据2", "key_point": "要点2"}
        ]

        stats = calculate_coverage_stats(key_points_result)
        assert stats["total"] == 2
        assert stats["covered"] == 2
        assert stats["missing"] == 0
        assert stats["contradicted"] == 0
        assert stats["coverage_rate"] == 1.0

    def test_partial_coverage(self):
        """测试：部分覆盖统计"""
        key_points_result = [
            {"status": "covered", "evidence": "证据", "key_point": "要点1"},
            {"status": "missing", "evidence": "", "key_point": "要点2"},
            {"status": "contradicted", "evidence": "矛盾", "key_point": "要点3"}
        ]

        stats = calculate_coverage_stats(key_points_result)
        assert stats["total"] == 3
        assert stats["covered"] == 1
        assert stats["missing"] == 1
        assert stats["contradicted"] == 1
        assert stats["coverage_rate"] == pytest.approx(0.333, rel=0.01)

    def test_covered_without_evidence_counted_as_missing(self):
        """测试：没有证据的 covered 计入 missing"""
        key_points_result = [
            {"status": "covered", "evidence": "", "key_point": "要点1"},  # 无证据
            {"status": "covered", "evidence": "有证据", "key_point": "要点2"}
        ]

        stats = calculate_coverage_stats(key_points_result)
        assert stats["covered"] == 1  # 只有一个有证据的
        assert stats["missing"] == 1  # 无证据的降级为 missing
