"""
评测脚本：对 KnowBase Lite 企业知识助手进行三角色问答评测
用法：
    python evaluation/run_eval.py answers          # 仅阶段1：采集系统回答
    python evaluation/run_eval.py judge            # 仅阶段2：LLM 裁判评分出报告
    python evaluation/run_eval.py all              # 两阶段全跑（默认）
    可选参数：--limit N 只跑前 N 题（测试用）；--role 员工|客服|管理员 只跑某角色

graph 流程：
    加载评测集 golden_set.json（3角色×20题，含标准答案）
        → 阶段1 采集回答：按角色构造 RagSummarizeService(user_role)（模拟不同角色登录提问）
              角色映射：员工→employee，客服→service，管理员→admin
              → rag_summarize(question) 走真实检索→阈值过滤→版本校验→生成→引用链路
              → 每题保存回答+引用数+耗时 → 实时写入 answers.json
        → 阶段2 裁判评分：LLM 对比【标准答案 vs 系统回答】
              → 判定 correct(1分) / partial(0.5分) / wrong(0分)
        → 汇总各角色准确率与拒答率 → 输出 results.json 明细 + report.md 报告
"""
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# 保证可从项目根目录导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.rag_service import RagSummarizeService

# ==================== 路径常量 ====================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(BASE_DIR, "evaluation")
GOLDEN_PATH = os.path.join(EVAL_DIR, "golden_set.json")   # 评测集（问题+标准答案）
ANSWERS_PATH = os.path.join(EVAL_DIR, "answers.json")     # 阶段1产物：系统回答
RESULTS_PATH = os.path.join(EVAL_DIR, "results.json")     # 阶段2产物：裁判评分明细
REPORT_PATH = os.path.join(EVAL_DIR, "report.md")         # 最终评测报告

# 并发数（DashScope 有 QPM 限制，不宜过高）
MAX_WORKERS = 3

# 角色 → 系统角色编码 映射（与数据库 User.role 一致）
ROLE_CODE_MAP = {
    "员工": "employee",
    "客服": "service",
    "管理员": "admin",
}

# 写文件锁（多线程实时保存用）
_lock = threading.Lock()

# 裁判提示词：让 LLM 以标准答案为基准判定系统回答质量
JUDGE_PROMPT = """你是严格的问答评测裁判，负责判定企业知识库智能问答系统的回答质量。

【用户问题】
{question}

【标准答案】（评测基准，来自企业知识库文档）
{reference}

【系统回答】（被评测对象）
{system_answer}

判定标准：
1. correct：系统回答涵盖了标准答案的全部关键要点（数字、期限、比例、流程步骤等事实要点），且无事实性错误；
2. partial：涵盖了标准答案的部分要点（约一半及以上），或存在轻微不准确/表述模糊；
3. wrong：关键要点大量缺失、答非所问、与标准答案存在事实矛盾、或无依据地拒答。

注意事项：
- 只比较事实要点是否一致，措辞风格差异不影响判定；
- 系统回答中的引用标记（如【参考资料：xxx】）不影响判定，只看实质内容；
- 标准答案中未提及、但系统回答补充的内容不算错误（除非与标准答案矛盾）。

请只输出如下 JSON，不要输出任何其他内容：
{{"verdict": "correct或partial或wrong", "reason": "50字以内的判定理由"}}"""


def load_questions(limit=None, role=None):
    """
    加载评测集，返回扁平化的题目列表
    :param limit: 只取前 N 题（测试用），None 表示全部
    :param role: 只取指定角色的题目，None 表示全部角色
    :return: list[dict]，每题含 id/role/role_code/question/reference_answer/source
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
                "source": q.get("source", ""),
            })

    if limit:
        questions = questions[:limit]
    return questions


def ask_rag(service_cache, question, role_code):
    """
    调用企业知识助手获取回答（模拟指定角色用户提问）
    :param service_cache: dict，按角色缓存 RagSummarizeService 实例（避免重复初始化）
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
    }


def save_json(path, data):
    """线程安全的 JSON 落盘（实时保存，防中断丢失）"""
    with _lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def run_answers(questions):
    """
    阶段1：并发采集系统回答
    :param questions: 题目列表
    :return: answers list[dict]，含每题的问题/标准答案/系统回答/RAG摘要
    """
    # 若存在上次结果则续跑（跳过已有回答的题）
    answers = {}
    if os.path.exists(ANSWERS_PATH):
        with open(ANSWERS_PATH, "r", encoding="utf-8") as f:
            for item in json.load(f):
                if item.get("system_answer"):
                    answers[item["id"]] = item
        print(f"[续跑] 已有 {len(answers)} 题回答，跳过这些题目")

    service_cache = {}
    todo = [q for q in questions if q["id"] not in answers]
    print(f"[阶段1] 共 {len(questions)} 题，本次需采集 {len(todo)} 题，并发 {MAX_WORKERS}")

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
            "source": q["source"],
            "system_answer": system_answer,
            "rag_meta": rag_meta,
        }
        with _lock:
            answers[q["id"]] = item
            done_count[0] += 1
            print(f"  ({done_count[0]}/{len(todo)}) {q['id']} {q['role']} 完成")
        save_json(ANSWERS_PATH, list(answers.values()))
        return item

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(worker, q) for q in todo]
        for fut in as_completed(futures):
            fut.result()          # 异常在 worker 内已兜底，这里仅等待

    result = list(answers.values())
    print(f"[阶段1] 完成，共 {len(result)} 题回答已保存至 answers.json")
    return result


def judge_one(item):
    """
    单题裁判：让 LLM 对比标准答案与系统回答，输出判定
    :param item: 含 question/reference_answer/system_answer 的题目
    :return: dict，含 verdict/score/reason
    """
    # 系统回答为空或采集异常，直接判 wrong
    if not item.get("system_answer") or item["system_answer"].startswith("[评测采集异常]"):
        return {"verdict": "wrong", "score": 0.0, "reason": "系统未产生有效回答"}

    prompt = JUDGE_PROMPT.format(
        question=item["question"],
        reference=item["reference_answer"],
        system_answer=item["system_answer"],
    )

    try:
        # temperature=0 保证裁判判定尽量稳定
        resp = chat_model_invoke(prompt)
        text = resp.strip()
        # 容错解析：模型可能用 ```json 包裹，提取第一对花括号内的内容
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"裁判输出无法解析：{text[:100]}")
        verdict_data = json.loads(text[start:end + 1])

        verdict = verdict_data.get("verdict", "wrong")
        if verdict not in ("correct", "partial", "wrong"):
            verdict = "wrong"
        score = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}[verdict]
        return {"verdict": verdict, "score": score, "reason": verdict_data.get("reason", "")}
    except Exception as e:
        return {"verdict": "wrong", "score": 0.0, "reason": f"[裁判异常] {e}"}


def chat_model_invoke(prompt):
    """调用大模型（与项目同款 chat_model），返回文本"""
    from model.factory import chat_model
    resp = chat_model.invoke(prompt)
    return resp.content


def run_judge(answers):
    """
    阶段2：LLM 裁判逐题评分，汇总各角色准确率并生成报告
    :param answers: 阶段1 的回答列表
    :return: results list[dict]（含评分明细）
    """
    print(f"[阶段2] 裁判评分 {len(answers)} 题，并发 {MAX_WORKERS}")

    verdicts = {}

    def worker(item):
        """单题评分任务"""
        return item["id"], judge_one(item)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(worker, item) for item in answers]
        for fut in as_completed(futures):
            qid, verdict = fut.result()
            verdicts[qid] = verdict

    # 合并评分到结果明细
    results = []
    for item in answers:
        verdict = verdicts[item["id"]]
        results.append({**{k: v for k, v in item.items() if k != "trace"}, **verdict})

    save_json(RESULTS_PATH, results)
    build_report(results)
    print_summary(results)
    return results


def build_report(results):
    """
    生成 markdown 评测报告：总览（准确率+拒答率）+ 各角色统计 + 每题明细
    :param results: 含评分的结果列表
    """
    roles_order = ["员工", "客服", "管理员"]

    # 按角色汇总统计
    stats = {}
    for r in results:
        s = stats.setdefault(r["role"], {"total": 0, "correct": 0, "partial": 0, "wrong": 0,
                                         "score": 0.0, "refused": 0, "total_time_ms": 0})
        s["total"] += 1
        s[r["verdict"]] += 1
        s["score"] += r["score"]
        rag_meta = r.get("rag_meta") or {}
        if not rag_meta.get("has_answer", True):
            s["refused"] += 1
        s["total_time_ms"] += rag_meta.get("total_time_ms", 0)

    lines = ["# KnowBase Lite 三角色问答评测报告", "",
             f"- 评测时间：2026-09-10",
             f"- 评测题数：{len(results)}（员工/客服/管理员各 20 题）",
             f"- 评分规则：correct=1 分，partial=0.5 分，wrong=0 分",
             "", "## 总览", "",
             "| 角色 | 总题数 | 完全正确 | 部分正确 | 错误 | 加权准确率 | 拒答数 | 平均耗时(ms) |",
             "|---|---|---|---|---|---|---|---|"]

    total_all = {"total": 0, "correct": 0, "partial": 0, "wrong": 0, "score": 0.0, "refused": 0, "total_time_ms": 0}
    for role in roles_order:
        s = stats.get(role)
        if not s:
            continue
        acc = s["score"] / s["total"] * 100
        avg_ms = s["total_time_ms"] / s["total"]
        lines.append(f"| {role} | {s['total']} | {s['correct']} | {s['partial']} | {s['wrong']} | {acc:.1f}% | {s['refused']} | {avg_ms:.0f} |")
        for k in total_all:
            total_all[k] += s[k]

    overall_acc = total_all["score"] / total_all["total"] * 100
    overall_avg = total_all["total_time_ms"] / total_all["total"]
    lines.append(f"| **总计** | {total_all['total']} | {total_all['correct']} | {total_all['partial']} | {total_all['wrong']} | **{overall_acc:.1f}%** | {total_all['refused']} | {overall_avg:.0f} |")

    # 每题明细
    lines += ["", "## 明细", ""]
    for role in roles_order:
        lines.append(f"### {role}")
        lines.append("")
        for r in [x for x in results if x["role"] == role]:
            icon = {"correct": "✅", "partial": "⚠️", "wrong": "❌"}[r["verdict"]]
            lines.append(f"- {icon} **{r['id']}** [{r['verdict']}] {r['question']}")
            lines.append(f"  - 判定理由：{r['reason']}")
            if r["verdict"] != "correct":
                lines.append(f"  - 标准答案：{r['reference_answer']}")
                sys_answer = (r.get("system_answer") or "")[:200]
                lines.append(f"  - 系统回答：{sys_answer}")
        lines.append("")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[报告] 已生成 {REPORT_PATH}")


def print_summary(results):
    """控制台打印各角色准确率摘要"""
    print("\n========== 评测结果摘要 ==========")
    for role in ["员工", "客服", "管理员"]:
        group = [r for r in results if r["role"] == role]
        if not group:
            continue
        acc = sum(r["score"] for r in group) / len(group) * 100
        c = sum(1 for r in group if r["verdict"] == "correct")
        p = sum(1 for r in group if r["verdict"] == "partial")
        w = sum(1 for r in group if r["verdict"] == "wrong")
        refused = sum(1 for r in group if not (r.get("rag_meta") or {}).get("has_answer", True))
        print(f"{role}：准确率 {acc:.1f}%（完全正确 {c} / 部分正确 {p} / 错误 {w} / 拒答 {refused}）")
    overall = sum(r["score"] for r in results) / len(results) * 100
    print(f"总体：准确率 {overall:.1f}%（{len(results)} 题）")


if __name__ == "__main__":
    # 命令行参数解析：阶段 + 可选 limit/role
    args = sys.argv[1:]
    stage = args[0] if args and args[0] in ("answers", "judge", "all") else "all"
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    role = args[args.index("--role") + 1] if "--role" in args else None

    if stage in ("answers", "all"):
        qs = load_questions(limit=limit, role=role)
        ans = run_answers(qs)
    else:
        with open(ANSWERS_PATH, "r", encoding="utf-8") as f:
            ans = json.load(f)

    if stage in ("judge", "all"):
        run_judge(ans)
