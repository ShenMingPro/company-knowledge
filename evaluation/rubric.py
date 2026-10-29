"""
评分规则模块：基于关键要点覆盖率的确定性判分

核心原则：
1. 裁判模型只负责逐要点判断（covered/missing/contradicted）
2. Python 代码根据覆盖情况计算最终 verdict
3. 规则明确、可测试、可回归验证
"""
from typing import List, Dict, Literal


def calculate_verdict(
    key_points_result: List[Dict[str, any]],
    min_partial_coverage: float = 0.0
) -> Literal["correct", "partial", "wrong"]:
    """
    根据关键要点覆盖情况计算最终 verdict

    判分规则：
    - correct: 所有关键要点 covered，且无关键事实 contradicted
    - partial: 至少覆盖一个有效关键要点（有证据），但未达到 correct
    - wrong: 零个关键要点 covered，或关键事实 contradicted，或明显答非所问

    :param key_points_result: 逐要点判断结果列表，每项包含 {status, evidence, key_point}
    :param min_partial_coverage: partial 最低覆盖率（默认 0.0，即至少 1 个要点即可）
    :return: "correct" | "partial" | "wrong"
    """
    if not key_points_result:
        # 没有关键要点定义，无法判断
        return "wrong"

    total_points = len(key_points_result)
    covered_count = 0
    contradicted_count = 0

    for item in key_points_result:
        status = item.get("status", "missing")
        evidence = item.get("evidence", "").strip()

        # 没有证据片段的 covered 自动降级为 missing
        if status == "covered":
            if evidence:
                covered_count += 1
            else:
                # 降级：没有证据支持的 covered 不计入
                pass
        elif status == "contradicted":
            contradicted_count += 1

    coverage_rate = covered_count / total_points if total_points > 0 else 0.0

    # 规则1：有关键事实矛盾 -> wrong
    if contradicted_count > 0:
        return "wrong"

    # 规则2：零覆盖 -> wrong
    if covered_count == 0:
        return "wrong"

    # 规则3：全覆盖 -> correct
    if covered_count == total_points:
        return "correct"

    # 规则4：部分覆盖 -> partial
    # 只要覆盖至少 1 个要点（上面已排除 covered_count == 0）
    # 且覆盖率 >= min_partial_coverage（默认 0.0）即判为 partial
    if covered_count > 0 and coverage_rate >= min_partial_coverage:
        return "partial"
    else:
        # 理论上不会到这里，因为 covered_count > 0 已经满足
        return "wrong"


def validate_key_points_result(key_points_result: List[Dict]) -> bool:
    """
    验证逐要点判断结果的格式合法性

    :param key_points_result: 逐要点判断结果
    :return: 是否合法
    """
    if not isinstance(key_points_result, list):
        return False

    for item in key_points_result:
        if not isinstance(item, dict):
            return False

        # 必须包含 status 和 key_point
        if "status" not in item or "key_point" not in item:
            return False

        # status 必须是合法值
        status = item.get("status")
        if status not in ["covered", "missing", "contradicted"]:
            return False

    return True


def calculate_coverage_stats(key_points_result: List[Dict]) -> Dict[str, any]:
    """
    计算关键要点覆盖统计信息

    :param key_points_result: 逐要点判断结果
    :return: 统计信息 {total, covered, missing, contradicted, coverage_rate}
    """
    total = len(key_points_result)
    covered = 0
    missing = 0
    contradicted = 0

    for item in key_points_result:
        status = item.get("status", "missing")
        evidence = item.get("evidence", "").strip()

        # 没有证据的 covered 降级为 missing
        if status == "covered" and evidence:
            covered += 1
        elif status == "covered" and not evidence:
            missing += 1
        elif status == "missing":
            missing += 1
        elif status == "contradicted":
            contradicted += 1

    coverage_rate = covered / total if total > 0 else 0.0

    return {
        "total": total,
        "covered": covered,
        "missing": missing,
        "contradicted": contradicted,
        "coverage_rate": round(coverage_rate, 3)
    }
