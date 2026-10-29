"""
评测脚本 V2：基于关键要点的稳定性评测

新特性：
1. 生成稳定性：temperature=0，使用 answer 模型
2. 裁判稳定性：逐要点判断 + Python 计算 verdict
3. 支持 --fresh 参数强制重新生成答案
4. 记录模型配置和 prompt 版本
5. 支持连续运行验证稳定性

用法：
    python evaluation/run_eval_v2.py answers          # 阶段1：采集系统回答
    python evaluation/run_eval_v2.py judge            # 阶段2：逐要点裁判
    python evaluation/run_eval_v2.py all              # 两阶段全跑（默认）
    python evaluation/run_eval_v2.py all --fresh      # 强制重新生成答案
    python evaluation/run_eval_v2.py stability       # 稳定性测试（连续运行5次）

可选参数：
    --limit N      只跑前 N 题（测试用）
    --role 员工|客服|管理员   只跑某角色
    --fresh        强制重新生成答案，不使用缓存
    --run-id ID    指定运行ID（用于区分不同批次）
"""
import json
import os
import sys
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 保证可从项目根目录导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.rag_service import RagSummarizeService
from model.factory import create_chat_model
from evaluation.rubric import calculate_verdict, validate_key_points_result, calculate_coverage_stats

# ==================== 路径常量 ====================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(BASE_DIR, "evaluation")
GOLDEN_PATH = os.path.join(EVAL_DIR, "golden_set.json")

# ==================== 配置 ====================
MAX_WORKERS = 3  # 并发线程数
ROLE_CODE_MAP = {"员工": "employee", "客服": "service", "管理员": "admin"}
_lock = threading.Lock()


def get_output_paths(run_id=None):
    """获取输出文件路径"""
    if run_id:
        answers_path = os.path.join(EVAL_DIR, f"answers_{run_id}.json")
        results_path = os.path.join(EVAL_DIR, f"results_{run_id}.json")
        report_path = os.path.join(EVAL_DIR, f"report_{run_id}.md")
    else:
        answers_path = os.path.join(EVAL_DIR, "answers_v2.json")
        results_path = os.path.join(EVAL_DIR, "results_v2.json")
        report_path = os.path.join(EVAL_DIR, "report_v2.md")

    return answers_path, results_path, report_path


def load_questions(limit=None, role=None):
    """
    加载评测集，返回扁平化的题目列表
    :param limit: 只取前 N 题（测试用），None 表示全部
    :param role: 只取指定角色的题目，None 表示全部角色
    :return: list[dict]，每题含 id/role/role_code/question/reference_answer/key_points/source
    """
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    questions = []
    for role_block in data["roles"]:
        if role and role_block["role"] != role:
            continue
        for q in role_block["questions"]:
            questions.append({
                "id": q["id"],
                "role": role_block["role"],
                "role_code": role_block.get("role_code", ROLE_CODE_MAP.get(role_block["role"], "employee")),
                "question": q["question"],
                "reference_answer": q["reference_answer"],
                "key_points": q.get("key_points", []),
                "source": q.get("source", ""),
            })

    if limit:
        questions = questions[:limit]
    return questions


def get_model_metadata():
    """获取模型配置元数据（不包含 API Key）"""
    from utils.config_handler import rag_conf

    metadata = {
        "chat_model_name": rag_conf.get("chat_model_name", "unknown"),
        "chat_model_temperature": rag_conf.get("chat_model_temperature", None),
        "chat_model_top_p": rag_conf.get("chat_model_top_p", None),
        "judge_model_temperature": rag_conf.get("judge_model_temperature", None),
        "judge_model_top_p": rag_conf.get("judge_model_top_p", None),
        "timestamp": datetime.now().isoformat(),
    }

    # 读取 prompt 版本（通过文件修改时间）
    prompt_path = os.path.join(BASE_DIR, "prompts", "rag_summarize.txt")
    if os.path.exists(prompt_path):
        metadata["prompt_modified"] = datetime.fromtimestamp(
            os.path.getmtime(prompt_path)
        ).isoformat()

    return metadata


def ask_rag(service_cache, question, role_code):
    """
    调用企业知识助手获取回答（模拟指定角色用户提问）
    使用 temperature=0 的 answer 模型

    :param service_cache: dict，按角色缓存 RagSummarizeService 实例
    :param question: 用户问题
    :param role_code: 角色编码（employee/service/admin）
    :return: (最终回答, RAG结果摘要dict)
    """
    with _lock:
        if role_code not in service_cache:
            service_cache[role_code] = RagSummarizeService(user_role=role_code)
        service = service_cache[role_code]

    result = service.rag_summarize(question)
    return result["answer"], {
        "has_answer": result.get("has_answer", False),
        "retrieved_count": result.get("retrieved_count", 0),
        "valid_count": result.get("valid_count", 0),
        "reference_count": len(result.get("references", [])),
        "total_time_ms": result.get("total_time_ms", 0),
        "refusal_reason": result.get("refusal_reason"),
        "retrieval_mode": result.get("retrieval_mode"),
    }


def save_json(path, data):
    """线程安全的 JSON 落盘（实时保存，防中断丢失）"""
    with _lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def run_answers(questions, fresh=False, run_id=None):
    """
    阶段1：并发采集系统回答
    :param questions: 题目列表
    :param fresh: 是否强制重新生成（不使用缓存）
    :param run_id: 运行ID
    :return: answers list[dict]，含每题的问题/标准答案/系统回答/RAG摘要
    """
    answers_path, _, _ = get_output_paths(run_id)

    # 若存在上次结果且不是 fresh 模式，则续跑
    answers = {}
    if not fresh and os.path.exists(answers_path):
        with open(answers_path, "r", encoding="utf-8") as f:
            for item in json.load(f):
                if item.get("system_answer"):
                    answers[item["id"]] = item
        print(f"[续跑] 已有 {len(answers)} 题回答，跳过这些题目")

    service_cache = {}
    todo = [q for q in questions if q["id"] not in answers]
    print(f"[阶段1] 共 {len(questions)} 题，本次需采集 {len(todo)} 题，并发 {MAX_WORKERS}")

    # 记录模型配置
    metadata = get_model_metadata()
    print(f"[模型配置] {metadata['chat_model_name']}, temp={metadata['chat_model_temperature']}, top_p={metadata['chat_model_top_p']}")

    done_count = [0]

    def worker(q):
        """单题采集任务：以角色身份调用系统 → 实时落盘"""
        try:
            system_answer, rag_meta = ask_rag(service_cache, q["question"], q["role_code"])
        except Exception as e:
            system_answer, rag_meta = f"[评测采集异常] {e}", {"has_answer": False}

        item = {
            "id": q["id"],
            "role": q["role"],
            "role_code": q["role_code"],
            "question": q["question"],
            "reference_answer": q["reference_answer"],
            "key_points": q["key_points"],
            "source": q["source"],
            "system_answer": system_answer,
            "rag_meta": rag_meta,
            "model_metadata": metadata,
        }

        with _lock:
            answers[q["id"]] = item
            done_count[0] += 1
            print(f"  ({done_count[0]}/{len(todo)}) {q['id']} {q['role']} 完成")

        save_json(answers_path, list(answers.values()))
        return item

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(worker, q) for q in todo]
        for fut in as_completed(futures):
            fut.result()

    result = list(answers.values())
    print(f"[阶段1] 完成，共 {len(result)} 题回答已保存至 {answers_path}")
    return result


def judge_one(item, judge_model):
    """
    单题裁判：使用 judge 模型逐要点判断，然后用 Python rubric 计算最终 verdict
    :param item: 含 question/reference_answer/key_points/system_answer 的题目
    :param judge_model: 裁判模型实例
    :return: dict，含 verdict/score/reason/key_points_result/coverage_stats/judge_status
    """
    # 系统回答为空或采集异常，直接判 wrong
    if not item.get("system_answer") or item["system_answer"].startswith("[评测采集异常]"):
        return {
            "verdict": "wrong",
            "score": 0.0,
            "reason": "系统未产生有效回答",
            "key_points_result": [],
            "coverage_stats": {},
            "judge_status": "skipped"
        }

    # 如果没有 key_points，使用传统裁判方式
    if not item.get("key_points"):
        return judge_one_legacy(item, judge_model)

    # 读取裁判提示词
    judge_prompt_path = os.path.join(EVAL_DIR, "judge_prompt.txt")
    with open(judge_prompt_path, "r", encoding="utf-8") as f:
        judge_prompt_template = f.read()

    # 格式化关键要点
    key_points_text = "\n".join([f"{i+1}. {kp}" for i, kp in enumerate(item["key_points"])])

    prompt = judge_prompt_template.format(
        question=item["question"],
        key_points=key_points_text,
        model_answer=item["system_answer"]
    )

    try:
        resp = judge_model.invoke(prompt)
        text = resp.content.strip()

        # 容错解析：提取第一对花括号内的内容
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"裁判输出无法解析：{text[:100]}")

        judge_data = json.loads(text[start:end + 1])
        key_points_result = judge_data.get("key_points_result", [])

        # 验证格式
        if not validate_key_points_result(key_points_result):
            raise ValueError(f"key_points_result 格式不合法")

        # 使用 Python rubric 计算最终 verdict
        verdict = calculate_verdict(key_points_result)
        score = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}[verdict]

        # 计算覆盖率统计
        coverage_stats = calculate_coverage_stats(key_points_result)

        # 生成简要理由
        reason = f"覆盖 {coverage_stats['covered']}/{coverage_stats['total']} 个要点 ({coverage_stats['coverage_rate']*100:.0f}%)"
        if coverage_stats['contradicted'] > 0:
            reason += f"，有 {coverage_stats['contradicted']} 个矛盾"

        return {
            "verdict": verdict,
            "score": score,
            "reason": reason,
            "key_points_result": key_points_result,
            "coverage_stats": coverage_stats,
            "judge_status": "success"
        }

    except Exception as e:
        # 裁判异常不应计入 wrong，而应该单独标记
        return {
            "verdict": "judge_error",
            "score": 0.0,
            "reason": f"[裁判异常] {str(e)[:100]}",
            "key_points_result": [],
            "coverage_stats": {},
            "judge_status": "error",
            "judge_error": str(e)
        }


def judge_one_legacy(item, judge_model):
    """
    传统裁判方式（用于没有 key_points 的题目）
    """
    # 使用简化的提示词
    prompt = f"""你是专业的答案评测裁判。请判断系统回答是否正确。

用户问题：{item['question']}
标准答案：{item['reference_answer']}
系统回答：{item['system_answer']}

判定标准：
- correct: 系统回答覆盖标准答案的所有关键要点
- partial: 系统回答覆盖标准答案的部分关键要点
- wrong: 系统回答完全不正确或完全拒答

请只输出 JSON：
{{"verdict": "correct或partial或wrong", "reason": "50字以内的判定理由"}}"""

    try:
        resp = judge_model.invoke(prompt)
        text = resp.content.strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"裁判输出无法解析")

        verdict_data = json.loads(text[start:end + 1])
        verdict = verdict_data.get("verdict", "wrong")
        if verdict not in ("correct", "partial", "wrong"):
            verdict = "wrong"

        score = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}[verdict]
        return {
            "verdict": verdict,
            "score": score,
            "reason": verdict_data.get("reason", ""),
            "key_points_result": [],
            "coverage_stats": {},
            "judge_status": "legacy"
        }
    except Exception as e:
        return {
            "verdict": "judge_error",
            "score": 0.0,
            "reason": f"[裁判异常] {str(e)[:100]}",
            "key_points_result": [],
            "coverage_stats": {},
            "judge_status": "error",
            "judge_error": str(e)
        }


def run_judge(answers, run_id=None):
    """
    阶段2：使用 judge 模型逐题评分，汇总统计并生成报告
    :param answers: 阶段1 的回答列表
    :param run_id: 运行ID
    :return: results list[dict]（含评分明细）
    """
    _, results_path, _ = get_output_paths(run_id)

    print(f"[阶段2] 裁判评分 {len(answers)} 题，并发 {MAX_WORKERS}")

    # 创建 judge 模型
    judge_model = create_chat_model(purpose="judge")

    verdicts = {}

    def worker(item):
        """单题评分任务"""
        return item["id"], judge_one(item, judge_model)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(worker, item) for item in answers]
        for fut in as_completed(futures):
            qid, verdict = fut.result()
            verdicts[qid] = verdict
            print(f"  {qid} -> {verdict['verdict']} ({verdict['reason']})")

    # 合并评分到结果明细
    results = []
    for item in answers:
        verdict = verdicts[item["id"]]
        results.append({
            **{k: v for k, v in item.items()},
            **verdict
        })

    save_json(results_path, results)
    build_report(results, run_id)
    print_summary(results)
    return results


def build_report(results, run_id=None):
    """
    生成 markdown 评测报告：总览 + 各角色统计 + 每题明细
    :param results: 含评分的结果列表
    :param run_id: 运行ID
    """
    _, _, report_path = get_output_paths(run_id)

    roles_order = ["员工", "客服", "管理员"]

    # 按角色汇总统计
    stats = {}
    judge_errors = []

    for r in results:
        s = stats.setdefault(r["role"], {
            "total": 0, "correct": 0, "partial": 0, "wrong": 0,
            "judge_error": 0, "score": 0.0, "refused": 0, "total_time_ms": 0
        })
        s["total"] += 1

        if r["verdict"] == "judge_error":
            s["judge_error"] += 1
            judge_errors.append(r["id"])
        else:
            s[r["verdict"]] += 1
            s["score"] += r["score"]

        rag_meta = r.get("rag_meta") or {}
        if not rag_meta.get("has_answer", True):
            s["refused"] += 1
        s["total_time_ms"] += rag_meta.get("total_time_ms", 0)

    # 获取模型配置
    model_meta = results[0].get("model_metadata", {}) if results else {}

    lines = [
        "# KnowBase Lite 三角色问答评测报告 V2",
        "",
        f"- 评测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 评测题数：{len(results)}",
        f"- 评分规则：correct=1 分，partial=0.5 分，wrong=0 分",
        f"- 模型：{model_meta.get('chat_model_name', 'unknown')}",
        f"- 生成配置：temperature={model_meta.get('chat_model_temperature')}, top_p={model_meta.get('chat_model_top_p')}",
        f"- 裁判配置：temperature={model_meta.get('judge_model_temperature')}, top_p={model_meta.get('judge_model_top_p')}",
        "",
        "## 总览",
        "",
        "| 角色 | 总题数 | 完全正确 | 部分正确 | 错误 | 裁判异常 | 加权准确率 | 拒答数 | 平均耗时(ms) |",
        "|---|---|---|---|---|---|---|---|---|"
    ]

    total_all = {
        "total": 0, "correct": 0, "partial": 0, "wrong": 0,
        "judge_error": 0, "score": 0.0, "refused": 0, "total_time_ms": 0
    }

    for role in roles_order:
        s = stats.get(role)
        if not s:
            continue

        # 计算准确率时排除裁判异常
        valid_total = s["total"] - s["judge_error"]
        acc = (s["score"] / valid_total * 100) if valid_total > 0 else 0
        avg_ms = s["total_time_ms"] / s["total"] if s["total"] > 0 else 0

        lines.append(
            f"| {role} | {s['total']} | {s['correct']} | {s['partial']} | "
            f"{s['wrong']} | {s['judge_error']} | {acc:.1f}% | {s['refused']} | {avg_ms:.0f} |"
        )

        for k in total_all:
            total_all[k] += s[k]

    valid_total = total_all["total"] - total_all["judge_error"]
    overall_acc = (total_all["score"] / valid_total * 100) if valid_total > 0 else 0
    overall_avg = total_all["total_time_ms"] / total_all["total"] if total_all["total"] > 0 else 0

    lines.append(
        f"| **总计** | {total_all['total']} | {total_all['correct']} | {total_all['partial']} | "
        f"{total_all['wrong']} | {total_all['judge_error']} | **{overall_acc:.1f}%** | "
        f"{total_all['refused']} | {overall_avg:.0f} |"
    )

    # 裁判异常说明
    if judge_errors:
        lines += [
            "",
            "## 裁判异常说明",
            "",
            f"以下 {len(judge_errors)} 题出现裁判异常（JSON 解析失败、超时等），已从准确率计算中排除：",
            "",
            ", ".join(judge_errors),
            ""
        ]

    # 每题明细
    lines += ["", "## 明细", ""]
    for role in roles_order:
        lines.append(f"### {role}")
        lines.append("")

        for r in [x for x in results if x["role"] == role]:
            if r["verdict"] == "judge_error":
                icon = "⚠️"
            else:
                icon = {"correct": "✅", "partial": "⚠️", "wrong": "❌"}[r["verdict"]]

            lines.append(f"- {icon} **{r['id']}** [{r['verdict']}] {r['question']}")
            lines.append(f"  - 判定理由：{r['reason']}")

            # 显示关键要点覆盖详情
            if r.get("key_points_result"):
                covered = [kp for kp in r["key_points_result"] if kp["status"] == "covered"]
                missing = [kp for kp in r["key_points_result"] if kp["status"] == "missing"]
                contradicted = [kp for kp in r["key_points_result"] if kp["status"] == "contradicted"]

                if covered:
                    lines.append(f"  - 已覆盖要点：{', '.join([kp['key_point'] for kp in covered])}")
                if missing:
                    lines.append(f"  - 缺失要点：{', '.join([kp['key_point'] for kp in missing])}")
                if contradicted:
                    lines.append(f"  - 矛盾要点：{', '.join([kp['key_point'] for kp in contradicted])}")

            if r["verdict"] != "correct":
                sys_answer = (r.get("system_answer") or "")[:200]
                lines.append(f"  - 系统回答：{sys_answer}...")

        lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[报告] 已生成 {report_path}")


def print_summary(results):
    """控制台打印各角色准确率摘要"""
    print("\n========== 评测结果摘要 ==========")

    for role in ["员工", "客服", "管理员"]:
        group = [r for r in results if r["role"] == role]
        if not group:
            continue

        # 排除裁判异常
        valid_group = [r for r in group if r["verdict"] != "judge_error"]
        judge_errors = len(group) - len(valid_group)

        if valid_group:
            acc = sum(r["score"] for r in valid_group) / len(valid_group) * 100
            c = sum(1 for r in valid_group if r["verdict"] == "correct")
            p = sum(1 for r in valid_group if r["verdict"] == "partial")
            w = sum(1 for r in valid_group if r["verdict"] == "wrong")
            refused = sum(1 for r in group if not (r.get("rag_meta") or {}).get("has_answer", True))

            print(f"{role}：准确率 {acc:.1f}%（完全正确 {c} / 部分正确 {p} / 错误 {w} / 裁判异常 {judge_errors} / 拒答 {refused}）")
        else:
            print(f"{role}：全部题目裁判异常")

    # 排除裁判异常计算总体准确率
    valid_results = [r for r in results if r["verdict"] != "judge_error"]
    if valid_results:
        overall = sum(r["score"] for r in valid_results) / len(valid_results) * 100
        judge_error_count = len(results) - len(valid_results)
        print(f"总体：准确率 {overall:.1f}%（有效题目 {len(valid_results)} / 裁判异常 {judge_error_count}）")
    else:
        print(f"总体：全部题目裁判异常")


def run_stability_test(questions, repeat=5):
    """
    稳定性测试：连续运行多次，验证生成和裁判的稳定性
    :param questions: 题目列表
    :param repeat: 重复次数
    """
    print(f"\n========== 开始稳定性测试 ==========")
    print(f"将连续运行 {repeat} 次，每次使用独立的 run_id")
    print(f"测试题目数：{len(questions)}")

    all_runs = []

    for i in range(repeat):
        run_id = f"stability_{i+1}"
        print(f"\n>>> 第 {i+1}/{repeat} 次运行（run_id={run_id}）")

        # 强制重新生成答案
        answers = run_answers(questions, fresh=True, run_id=run_id)
        results = run_judge(answers, run_id=run_id)

        all_runs.append({
            "run_id": run_id,
            "results": results
        })

    # 分析稳定性
    print("\n========== 稳定性分析 ==========")
    analyze_stability(all_runs, questions)


def analyze_stability(all_runs, questions):
    """
    分析稳定性：比较多次运行的 verdict 一致性
    """
    # 按题目ID组织结果
    by_question = {}
    for q in questions:
        by_question[q["id"]] = {
            "question": q["question"],
            "verdicts": [],
            "answers": []
        }

    for run in all_runs:
        for r in run["results"]:
            qid = r["id"]
            if qid in by_question:
                by_question[qid]["verdicts"].append(r["verdict"])
                by_question[qid]["answers"].append(r.get("system_answer", ""))

    # 统计一致性
    verdict_consistent = 0
    verdict_inconsistent = 0

    print("\n### Verdict 稳定性")
    for qid, data in by_question.items():
        verdicts = data["verdicts"]
        unique_verdicts = set(verdicts)

        if len(unique_verdicts) == 1:
            verdict_consistent += 1
        else:
            verdict_inconsistent += 1
            print(f"  ❌ {qid}: {verdicts} (不一致)")

    print(f"\nVerdict 一致：{verdict_consistent}/{len(by_question)} 题 ({verdict_consistent/len(by_question)*100:.1f}%)")
    print(f"Verdict 不一致：{verdict_inconsistent}/{len(by_question)} 题 ({verdict_inconsistent/len(by_question)*100:.1f}%)")

    # 特别检查 emp_06
    if "emp_06" in by_question:
        emp_06_verdicts = by_question["emp_06"]["verdicts"]
        print(f"\n### emp_06 特别检查")
        print(f"  连续 {len(emp_06_verdicts)} 次运行的 verdict: {emp_06_verdicts}")
        if all(v == "partial" for v in emp_06_verdicts):
            print(f"  ✅ emp_06 稳定判为 partial")
        else:
            print(f"  ❌ emp_06 判定不稳定")


if __name__ == "__main__":
    # 命令行参数解析
    args = sys.argv[1:]
    stage = args[0] if args and args[0] in ("answers", "judge", "all", "stability") else "all"

    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    role = args[args.index("--role") + 1] if "--role" in args else None
    fresh = "--fresh" in args
    run_id = args[args.index("--run-id") + 1] if "--run-id" in args else None

    if stage == "stability":
        # 稳定性测试模式
        qs = load_questions(limit=limit, role=role)
        run_stability_test(qs, repeat=5)
    elif stage in ("answers", "all"):
        qs = load_questions(limit=limit, role=role)
        ans = run_answers(qs, fresh=fresh, run_id=run_id)

        if stage == "all":
            run_judge(ans, run_id=run_id)
    else:
        # 只运行裁判阶段
        answers_path, _, _ = get_output_paths(run_id)
        if not os.path.exists(answers_path):
            print(f"错误：未找到答案文件 {answers_path}")
            sys.exit(1)

        with open(answers_path, "r", encoding="utf-8") as f:
            ans = json.load(f)

        run_judge(ans, run_id=run_id)
