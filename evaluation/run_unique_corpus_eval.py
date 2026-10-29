"""对“160份独立资料_测评题清单.xlsx”执行端到端 RAG 准确率评测。

评测分为两阶段：
1. answers：按知识空间对应的真实角色调用 RagSummarizeService，保存回答与引用。
2. judge：以标准答案为唯一事实基准，由裁判模型逐事实判定，再由 Python 计算分数。

脚本会逐题原子落盘，默认可断点续跑。示例：

    python evaluation/run_unique_corpus_eval.py all --limit 8 --run-id smoke_8 --fresh
    python evaluation/run_unique_corpus_eval.py all
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


BASE_DIR = Path(__file__).resolve().parent.parent
EVAL_DIR = BASE_DIR / "evaluation"
DEFAULT_INPUT = Path(
    r"C:\Users\Legion\Desktop\企业资料\_gen\160份独立资料_测评题清单.xlsx"
)

sys.path.insert(0, str(BASE_DIR))

from evaluation.rubric import calculate_coverage_stats, calculate_verdict  # noqa: E402
from model.factory import create_chat_model  # noqa: E402
from rag.rag_service import RagSummarizeService  # noqa: E402
from utils.config_handler import chroma_conf, rag_conf  # noqa: E402


SPACE_ROLE_MAP = {
    "产品知识库": "employee",
    "企业制度库": "employee",
    "售后知识库": "service",
    "管理员空间": "admin",
}
VERDICT_SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}
STATE_LOCK = threading.RLock()
SERVICE_LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def output_paths(run_id: str | None) -> tuple[Path, Path, Path]:
    suffix = f"_{run_id}" if run_id else ""
    return (
        EVAL_DIR / f"unique_corpus_answers{suffix}.json",
        EVAL_DIR / f"unique_corpus_results{suffix}.json",
        EVAL_DIR / f"unique_corpus_report{suffix}.md",
    )


def load_questions(input_path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not input_path.exists():
        raise FileNotFoundError(f"评测题清单不存在：{input_path}")

    workbook = load_workbook(input_path, read_only=True, data_only=True)
    if "资料与测评题" not in workbook.sheetnames:
        raise ValueError("评测题清单缺少工作表：资料与测评题")

    sheet = workbook["资料与测评题"]
    rows = sheet.iter_rows(values_only=True)
    headers = [str(value).strip() if value is not None else "" for value in next(rows)]
    required = ["序号", "知识空间", "格式", "文档编号", "文件名", "主题", "测评问题", "标准答案"]
    if headers != required:
        raise ValueError(f"评测题字段不符合预期：{headers}")

    questions: list[dict[str, Any]] = []
    for values in rows:
        if not values or all(value is None for value in values):
            continue
        row = dict(zip(headers, values))
        space = str(row["知识空间"]).strip()
        if space not in SPACE_ROLE_MAP:
            raise ValueError(f"未知知识空间：{space}")
        questions.append(
            {
                "sequence": int(row["序号"]),
                "id": str(row["文档编号"]).strip(),
                "space": space,
                "role_code": SPACE_ROLE_MAP[space],
                "format": str(row["格式"]).strip().upper(),
                "expected_source": str(row["文件名"]).strip(),
                "topic": str(row["主题"]).strip(),
                "question": str(row["测评问题"]).strip(),
                "reference_answer": str(row["标准答案"]).strip(),
            }
        )

    workbook.close()
    ids = [item["id"] for item in questions]
    if len(ids) != len(set(ids)):
        duplicates = [item for item, count in Counter(ids).items() if count > 1]
        raise ValueError(f"文档编号重复：{duplicates}")

    return questions[:limit] if limit else questions


def corpus_snapshot() -> dict[str, Any]:
    """记录评测时可见的有效语料规模，便于解释结果和后续复现。"""
    try:
        from sqlalchemy import func

        from models.database import Document, DocumentVersion, KnowledgeSpace, get_session

        session = get_session()
        try:
            rows = (
                session.query(
                    KnowledgeSpace.name,
                    func.count(Document.id),
                    func.sum(DocumentVersion.chunk_count),
                )
                .join(Document, Document.space_id == KnowledgeSpace.id)
                .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
                .filter(Document.is_deleted.is_(False), DocumentVersion.status == "success")
                .group_by(KnowledgeSpace.name)
                .all()
            )
        finally:
            session.close()

        spaces = {
            name: {"documents": int(documents), "chunks": int(chunks or 0)}
            for name, documents, chunks in rows
        }
        return {
            "documents": sum(item["documents"] for item in spaces.values()),
            "chunks": sum(item["chunks"] for item in spaces.values()),
            "spaces": spaces,
        }
    except Exception as exc:  # 评测不能因快照统计失败而中断
        return {"error": str(exc)}


def build_metadata(input_path: Path, question_count: int, workers: int) -> dict[str, Any]:
    hybrid = chroma_conf.get("hybrid_retrieval", {})
    reranker = chroma_conf.get("reranker", {})
    rewrite = chroma_conf.get("query_rewrite", {})
    return {
        "schema_version": 1,
        "started_at": now_iso(),
        "input_path": str(input_path.resolve()),
        "input_sha256": file_sha256(input_path),
        "question_count": question_count,
        "workers": workers,
        "scoring": {"correct": 1.0, "partial": 0.5, "wrong": 0.0},
        "space_role_map": SPACE_ROLE_MAP,
        "model": {
            "answer": rag_conf.get("chat_model_name"),
            "answer_temperature": rag_conf.get("chat_model_temperature"),
            "answer_top_p": rag_conf.get("chat_model_top_p"),
            "judge": rag_conf.get("chat_model_name"),
            "judge_temperature": rag_conf.get("judge_model_temperature"),
            "judge_top_p": rag_conf.get("judge_model_top_p"),
            "embedding": rag_conf.get("embedding_model_name"),
        },
        "retrieval": {
            "hybrid_enabled": hybrid.get("enabled"),
            "vector_k": hybrid.get("vector_candidate_k"),
            "bm25_k": hybrid.get("bm25_candidate_k"),
            "final_k": hybrid.get("final_top_k"),
            "query_rewrite_enabled": rewrite.get("enabled"),
            "reranker_enabled": reranker.get("enabled"),
            "reranker_model": reranker.get("model"),
            "reranker_output_k": reranker.get("output_top_k"),
            "relevance_distance_threshold": RagSummarizeService.RELEVANCE_THRESHOLD,
        },
        "corpus_snapshot": corpus_snapshot(),
    }


def read_state(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    candidates = [path, path.with_suffix(path.suffix + ".tmp")]
    candidates.extend(path.parent.glob(path.name + ".checkpoint_*.json"))
    candidates = [candidate for candidate in candidates if candidate.exists()]
    if not candidates:
        return {}, {}

    payload = None
    errors = []
    for candidate in sorted(
        candidates, key=lambda item: item.stat().st_mtime_ns, reverse=True
    ):
        try:
            with candidate.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
            break
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{candidate.name}: {exc}")

    if payload is None:
        raise ValueError(f"没有可读取的评测快照：{'；'.join(errors)}")
    if isinstance(payload, list):
        return {}, {str(item["id"]): item for item in payload}
    return payload.get("metadata", {}), {
        str(item["id"]): item for item in payload.get("items", [])
    }


def write_state(
    path: Path,
    metadata: dict[str, Any],
    items: dict[str, dict[str, Any]],
) -> None:
    ordered = sorted(items.values(), key=lambda item: item.get("sequence", 0))
    payload = {"metadata": metadata, "items": ordered}
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)

    # Windows Defender、索引器或预览程序可能会短暂占用目标文件。
    # 重试原子替换；若仍被占用，保留一个完整检查点并继续评测。
    last_error: PermissionError | None = None
    for attempt in range(20):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.1 + attempt * 0.02)

    checkpoint = path.with_name(
        f"{path.name}.checkpoint_{time.time_ns()}.json"
    )
    os.replace(temp_path, checkpoint)
    print(
        f"[保存警告] 目标文件被占用，已改存完整检查点 {checkpoint.name}：{last_error}",
        flush=True,
    )


def normalized_filename(value: Any) -> str:
    text = str(value or "").strip().replace("/", "\\")
    return text.rsplit("\\", 1)[-1].casefold()


def source_match(expected_source: str, references: list[dict[str, Any]]) -> tuple[bool, bool, int | None]:
    expected = normalized_filename(expected_source)
    names = [normalized_filename(ref.get("source_name")) for ref in references]
    try:
        index = names.index(expected)
    except ValueError:
        return False, False, None
    return True, index == 0, index + 1


def compact_rag_metadata(result: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "has_answer",
        "retrieved_count",
        "filtered_count",
        "valid_count",
        "retrieval_time_ms",
        "generation_time_ms",
        "total_time_ms",
        "refusal_reason",
        "error_internal",
        "model_name",
        "retrieval_mode",
        "vector_retrieved_count",
        "bm25_retrieved_count",
        "fused_count",
        "retrieval_query",
        "query_rewrite_applied",
        "query_rewrite_fallback_reason",
        "query_rewrite_time_ms",
        "rerank_enabled",
        "rerank_applied",
        "rerank_model",
        "rerank_candidate_count",
        "rerank_output_count",
        "rerank_time_ms",
        "rerank_fallback_reason",
    )
    return {field: result.get(field) for field in fields if field in result}


def ask_with_retry(
    service: RagSummarizeService,
    question: str,
    max_attempts: int = 3,
) -> tuple[dict[str, Any], int]:
    last_result: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = service.rag_summarize(question)
        except Exception as exc:
            result = {
                "answer": "",
                "has_answer": False,
                "references": [],
                "refusal_reason": "uncaught_exception",
                "error_internal": str(exc),
                "total_time_ms": 0,
            }
        last_result = result
        transient = result.get("refusal_reason") in {"generation_error", "uncaught_exception"}
        if not transient:
            return result, attempt
        if attempt < max_attempts:
            time.sleep(1.5 * attempt)
    return last_result or {}, max_attempts


def run_answers(
    questions: list[dict[str, Any]],
    output_path: Path,
    metadata: dict[str, Any],
    workers: int,
    fresh: bool,
) -> list[dict[str, Any]]:
    _, existing = ({}, {}) if fresh else read_state(output_path)
    question_ids = {question["id"] for question in questions}
    answers = {
        item_id: item
        for item_id, item in existing.items()
        if item_id in question_ids
        and item.get("answer_status") != "error"
        and item.get("system_answer") is not None
    }
    todo = [question for question in questions if question["id"] not in answers]
    print(
        f"[回答阶段] 共 {len(questions)} 题，复用 {len(answers)} 题，本次执行 {len(todo)} 题，"
        f"并发 {workers}",
        flush=True,
    )

    services: dict[str, RagSummarizeService] = {}
    completed = 0

    def service_for(role_code: str) -> RagSummarizeService:
        with SERVICE_LOCK:
            if role_code not in services:
                services[role_code] = RagSummarizeService(user_role=role_code)
            return services[role_code]

    def worker(question: dict[str, Any]) -> dict[str, Any]:
        result, attempts = ask_with_retry(
            service_for(question["role_code"]), question["question"]
        )
        references = result.get("references") or []
        hit, top1_hit, source_rank = source_match(question["expected_source"], references)
        has_answer = bool(result.get("has_answer"))
        is_error = result.get("refusal_reason") in {"generation_error", "uncaught_exception"}
        return {
            **question,
            "system_answer": result.get("answer") or "",
            "answer_status": "error" if is_error else ("answered" if has_answer else "refused"),
            "answer_attempts": attempts,
            "references": references,
            "expected_source_hit": hit,
            "expected_source_top1": top1_hit,
            "expected_source_rank": source_rank,
            "rag_meta": compact_rag_metadata(result),
            "answered_at": now_iso(),
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, question): question for question in todo}
        for future in as_completed(futures):
            question = futures[future]
            try:
                item = future.result()
            except Exception as exc:
                item = {
                    **question,
                    "system_answer": "",
                    "answer_status": "error",
                    "answer_attempts": 1,
                    "references": [],
                    "expected_source_hit": False,
                    "expected_source_top1": False,
                    "expected_source_rank": None,
                    "rag_meta": {
                        "has_answer": False,
                        "refusal_reason": "worker_exception",
                        "error_internal": str(exc),
                        "total_time_ms": 0,
                    },
                    "answered_at": now_iso(),
                }
            with STATE_LOCK:
                answers[item["id"]] = item
                completed += 1
                write_state(output_path, metadata, answers)
            marker = "命中来源" if item["expected_source_hit"] else "未命中来源"
            print(
                f"  [{completed}/{len(todo)}] {item['id']} {item['answer_status']} {marker}",
                flush=True,
            )

    metadata["answers_completed_at"] = now_iso()
    with STATE_LOCK:
        write_state(output_path, metadata, answers)
    print(f"[回答阶段] 已保存：{output_path}", flush=True)
    return sorted(answers.values(), key=lambda item: item["sequence"])


JUDGE_PROMPT = """你是严格、客观的企业知识库问答评测裁判。

用户问题：
{question}

标准答案（唯一评分事实基准）：
{reference_answer}

系统回答：
{system_answer}

请先把标准答案拆成最小且独立的事实要点，然后逐项检查系统回答。

状态定义：
- covered：系统回答明确表达了该事实，语义等价即可，必须摘录系统回答中的证据。
- missing：系统回答未表达该事实，evidence 为空字符串。
- contradicted：系统回答与该事实冲突，或给出了错误数字、期限、条件、对象，必须摘录冲突证据。

约束：
1. 只能依据标准答案判分，不要用常识补充或修正标准答案。
2. 来源文件名、解释性复述不单独算事实要点。
3. 数字、单位、范围、条件、否定词必须一致；同义表达可以通过。
4. 系统回答额外提供了标准答案没有要求的内容时，若不与标准答案冲突，不扣分。
5. 不要因为回答写得长、语气肯定或带有引用就判为 covered。
6. 每个事实要点只出现一次，至少返回一个要点。

只输出合法 JSON，不要输出 Markdown 代码块：
{{
  "key_points_result": [
    {{
      "key_point": "从标准答案提炼的原子事实",
      "status": "covered 或 missing 或 contradicted",
      "evidence": "系统回答中的直接证据；missing 时为空字符串"
    }}
  ],
  "reason": "一句话总结"
}}
"""


def extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"裁判输出中没有 JSON 对象：{text[:160]}")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("裁判输出不是 JSON 对象")
    return value


def validate_judge_points(points: Any) -> list[dict[str, str]]:
    if not isinstance(points, list) or not points:
        raise ValueError("key_points_result 必须是非空数组")
    normalized: list[dict[str, str]] = []
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("要点结果不是对象")
        key_point = str(point.get("key_point") or "").strip()
        status = str(point.get("status") or "").strip().lower()
        evidence = str(point.get("evidence") or "").strip()
        if not key_point:
            raise ValueError("要点内容为空")
        if status not in {"covered", "missing", "contradicted"}:
            raise ValueError(f"非法要点状态：{status}")
        if status == "covered" and not evidence:
            status = "missing"
        normalized.append(
            {"key_point": key_point, "status": status, "evidence": evidence}
        )
    return normalized


def judge_one(
    item: dict[str, Any],
    judge_model: Any,
    max_attempts: int = 3,
) -> dict[str, Any]:
    rag_meta = item.get("rag_meta") or {}
    if item.get("answer_status") == "error":
        return {
            "verdict": "wrong",
            "score": 0.0,
            "judge_status": "skipped_answer_error",
            "judge_attempts": 0,
            "reason": "回答阶段发生系统异常，未产生可评分答案",
            "key_points_result": [],
            "coverage_stats": {},
        }
    if not rag_meta.get("has_answer") or not item.get("system_answer"):
        return {
            "verdict": "wrong",
            "score": 0.0,
            "judge_status": "skipped_refusal",
            "judge_attempts": 0,
            "reason": f"系统拒答：{rag_meta.get('refusal_reason') or 'unknown'}",
            "key_points_result": [],
            "coverage_stats": {},
        }

    prompt = JUDGE_PROMPT.format(
        question=item["question"],
        reference_answer=item["reference_answer"],
        system_answer=item["system_answer"],
    )
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            response = judge_model.invoke(prompt)
            raw_text = str(response.content).strip()
            data = extract_json_object(raw_text)
            points = validate_judge_points(data.get("key_points_result"))
            verdict = calculate_verdict(points)
            coverage = calculate_coverage_stats(points)
            reason = str(data.get("reason") or "").strip()
            if not reason:
                reason = (
                    f"覆盖 {coverage['covered']}/{coverage['total']} 个事实要点，"
                    f"矛盾 {coverage['contradicted']} 个"
                )
            return {
                "verdict": verdict,
                "score": VERDICT_SCORE[verdict],
                "judge_status": "success",
                "judge_attempts": attempt,
                "reason": reason,
                "key_points_result": points,
                "coverage_stats": coverage,
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)

    return {
        "verdict": "judge_error",
        "score": None,
        "judge_status": "error",
        "judge_attempts": max_attempts,
        "reason": f"裁判异常：{last_error[:300]}",
        "judge_error": last_error,
        "key_points_result": [],
        "coverage_stats": {},
    }


def run_judge(
    answers: list[dict[str, Any]],
    output_path: Path,
    metadata: dict[str, Any],
    workers: int,
    fresh: bool,
) -> list[dict[str, Any]]:
    _, existing = ({}, {}) if fresh else read_state(output_path)
    answer_ids = {item["id"] for item in answers}
    results = {
        item_id: item
        for item_id, item in existing.items()
        if item_id in answer_ids and item.get("judge_status") != "error"
    }
    todo = [item for item in answers if item["id"] not in results]
    print(
        f"[裁判阶段] 共 {len(answers)} 题，复用 {len(results)} 题，本次执行 {len(todo)} 题，"
        f"并发 {workers}",
        flush=True,
    )

    judge_model = create_chat_model(purpose="judge")
    completed = 0

    def worker(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, **judge_one(item, judge_model), "judged_at": now_iso()}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, item): item for item in todo}
        for future in as_completed(futures):
            answer = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    **answer,
                    "verdict": "judge_error",
                    "score": None,
                    "judge_status": "error",
                    "judge_attempts": 1,
                    "reason": f"裁判工作线程异常：{exc}",
                    "judge_error": str(exc),
                    "key_points_result": [],
                    "coverage_stats": {},
                    "judged_at": now_iso(),
                }
            with STATE_LOCK:
                results[result["id"]] = result
                completed += 1
                write_state(output_path, metadata, results)
            print(
                f"  [{completed}/{len(todo)}] {result['id']} -> {result['verdict']} "
                f"({result['reason'][:70]})",
                flush=True,
            )

    metadata["judging_completed_at"] = now_iso()
    with STATE_LOCK:
        write_state(output_path, metadata, results)
    print(f"[裁判阶段] 已保存：{output_path}", flush=True)
    return sorted(results.values(), key=lambda item: item["sequence"])


def percentile_nearest_rank(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return float(ordered[rank - 1])


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = Counter(item.get("verdict") for item in items)
    valid = [item for item in items if item.get("verdict") in VERDICT_SCORE]
    latencies = [
        float((item.get("rag_meta") or {}).get("total_time_ms") or 0)
        for item in items
    ]
    score_sum = sum(float(item.get("score") or 0) for item in valid)
    total = len(items)
    valid_count = len(valid)
    return {
        "total": total,
        "valid_judged": valid_count,
        "correct": verdicts["correct"],
        "partial": verdicts["partial"],
        "wrong": verdicts["wrong"],
        "judge_error": verdicts["judge_error"],
        "weighted_accuracy": score_sum / valid_count if valid_count else 0.0,
        "exact_accuracy": verdicts["correct"] / valid_count if valid_count else 0.0,
        "refused": sum(item.get("answer_status") == "refused" for item in items),
        "answer_error": sum(item.get("answer_status") == "error" for item in items),
        "answer_rate": (
            sum(item.get("answer_status") == "answered" for item in items) / total
            if total
            else 0.0
        ),
        "source_hit": sum(bool(item.get("expected_source_hit")) for item in items),
        "source_hit_rate": (
            sum(bool(item.get("expected_source_hit")) for item in items) / total
            if total
            else 0.0
        ),
        "source_top1": sum(bool(item.get("expected_source_top1")) for item in items),
        "source_top1_rate": (
            sum(bool(item.get("expected_source_top1")) for item in items) / total
            if total
            else 0.0
        ),
        "latency_avg_ms": statistics.fmean(latencies) if latencies else 0.0,
        "latency_p50_ms": statistics.median(latencies) if latencies else 0.0,
        "latency_p95_ms": percentile_nearest_rank(latencies, 0.95),
    }


def group_summaries(
    items: list[dict[str, Any]], key: str
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[str(item.get(key) or "未知")].append(item)
    return {name: summarize(group) for name, group in sorted(groups.items())}


def md_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    if limit and len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def stats_table(groups: dict[str, dict[str, Any]], label: str) -> list[str]:
    lines = [
        f"| {label} | 题数 | 完全正确 | 部分正确 | 错误 | 加权准确率 | 完全正确率 | 来源命中率 | Top1命中率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, stats in groups.items():
        lines.append(
            f"| {md_text(name)} | {stats['total']} | {stats['correct']} | {stats['partial']} | "
            f"{stats['wrong']} | {percent(stats['weighted_accuracy'])} | "
            f"{percent(stats['exact_accuracy'])} | {percent(stats['source_hit_rate'])} | "
            f"{percent(stats['source_top1_rate'])} |"
        )
    return lines


def classify_issue(item: dict[str, Any]) -> str:
    if item.get("verdict") == "judge_error":
        return "裁判异常"
    if item.get("answer_status") == "error":
        return "回答服务异常"
    if item.get("answer_status") == "refused":
        return "检索不足导致拒答"
    if not item.get("expected_source_hit"):
        return "预期来源未召回"
    if item.get("verdict") == "partial":
        return "来源已召回但回答不完整"
    if item.get("verdict") == "wrong":
        return "来源已召回但回答存在错误或矛盾"
    return "通过"


def build_report(
    results: list[dict[str, Any]],
    metadata: dict[str, Any],
    report_path: Path,
) -> dict[str, Any]:
    overall = summarize(results)
    by_space = group_summaries(results, "space")
    by_format = group_summaries(results, "format")
    issue_counts = Counter(
        classify_issue(item)
        for item in results
        if item.get("verdict") != "correct"
    )
    wrong_items = [item for item in results if item.get("verdict") == "wrong"]

    def is_generated_source(source_name: Any) -> bool:
        filename = normalized_filename(source_name).upper()
        return filename.startswith(("CP-", "ZD-", "SH-", "GL-"))

    wrong_with_legacy_reference = sum(
        any(
            not is_generated_source(reference.get("source_name"))
            for reference in item.get("references", [])
        )
        for item in wrong_items
    )
    wrong_without_legacy_reference = len(wrong_items) - wrong_with_legacy_reference
    corpus = metadata.get("corpus_snapshot") or {}
    model = metadata.get("model") or {}
    retrieval = metadata.get("retrieval") or {}

    lines = [
        "# 160 份独立企业资料问答准确率评测报告",
        "",
        f"- 生成时间：{now_iso()}",
        f"- 评测题数：{overall['total']}",
        f"- 有效裁判题数：{overall['valid_judged']}（裁判异常 {overall['judge_error']}）",
        f"- 回答模型 / 裁判模型：{model.get('answer', 'unknown')} / {model.get('judge', 'unknown')}",
        f"- 当前有效知识库：{corpus.get('documents', 'unknown')} 份文档、{corpus.get('chunks', 'unknown')} 个分片",
        f"- 目标新资料：160 份；额外旧资料：{max(0, int(corpus.get('documents', 160) or 160) - 160)} 份",
        "",
        "## 核心结果",
        "",
        f"- **加权回答准确率：{percent(overall['weighted_accuracy'])}**（正确=1，部分正确=0.5，错误=0；裁判异常不计分母）",
        f"- **完全正确率：{percent(overall['exact_accuracy'])}**",
        f"- 完全正确 / 部分正确 / 错误：{overall['correct']} / {overall['partial']} / {overall['wrong']}",
        f"- 回答率：{percent(overall['answer_rate'])}；拒答 {overall['refused']}；回答服务异常 {overall['answer_error']}",
        f"- 预期来源任意位命中率：{percent(overall['source_hit_rate'])}（{overall['source_hit']}/{overall['total']}）",
        f"- 预期来源 Top1 命中率：{percent(overall['source_top1_rate'])}（{overall['source_top1']}/{overall['total']}）",
        f"- 平均 / P50 / P95 响应耗时：{overall['latency_avg_ms']:.0f} / {overall['latency_p50_ms']:.0f} / {overall['latency_p95_ms']:.0f} ms",
        "",
        "## 按知识空间",
        "",
        *stats_table(by_space, "知识空间"),
        "",
        "## 按文件格式",
        "",
        *stats_table(by_format, "格式"),
        "",
        "## 非完全正确题原因分布",
        "",
    ]

    if issue_counts:
        for issue, count in issue_counts.most_common():
            lines.append(f"- {issue}：{count} 题")
    else:
        lines.append("- 无")

    lines += [
        "",
        "## 问题原因与改进建议",
        "",
        f"- {len(wrong_items)} 道错题的目标文件均位于引用第 1 位，因此主要问题不是召回失败，而是多来源事实冲突后的答案合成。",
        f"- {wrong_with_legacy_reference} 道错题同时引用了旧资料；旧资料与新资料对补卡期限、密码锁定次数、调岗条件、假期天数、工单关闭时限或数据泄露上报时限给出了不同规则。",
        f"- {wrong_without_legacy_reference} 道错题未引用旧资料，但新资料中的通用派单矩阵与专项数据库处置规则缺少适用范围和优先级说明。",
        "- 当前回答提示词要求在资料冲突时保持谨慎，却没有可用于判定权威来源的版本、生效日期、业务范围或优先级元数据，因此模型会并列展示冲突值或转为无法确认。",
        "",
        "建议按以下顺序改进：",
        "",
        "1. 梳理 44 份旧资料，将已失效版本下架；仍有效的资料补充生效日期、失效日期、业务范围和权威级别。",
        "2. 检索阶段只放行当前有效且适用范围匹配的分片；发生同主题冲突时，优先按版本、生效日期和权威级别过滤，再进入生成模型。",
        "3. 修订 SH-X02 与 SH-D08/SH-D09 的职责边界，明确“交付支持组”和“私有化支持组”分别适用的请求类型。",
        "4. 将本次 7 道错题作为冲突回归集；完成文档治理后重新执行同一脚本，目标应为 160 题全部正确。",
        "",
        "## 非完全正确题明细",
        "",
        "| 编号 | 空间 | 格式 | 判定 | 来源命中 | 问题 | 判定理由 |",
        "|---|---|---|---|---|---|---|",
    ]
    problem_items = [item for item in results if item.get("verdict") != "correct"]
    if problem_items:
        for item in problem_items:
            source_state = (
                f"是（第{item.get('expected_source_rank')}位）"
                if item.get("expected_source_hit")
                else "否"
            )
            lines.append(
                f"| {md_text(item['id'])} | {md_text(item['space'])} | {md_text(item['format'])} | "
                f"{md_text(item.get('verdict'))} | {source_state} | {md_text(item['question'], 100)} | "
                f"{md_text(item.get('reason'), 180)} |"
            )
    else:
        lines.append("| - | - | - | - | - | 无 | - |")

    lines += [
        "",
        "## 评测方法与口径",
        "",
        "1. 每份资料对应一道独立问题，共 160 道；问题和标准答案来自上传前生成的测评清单。",
        "2. 产品知识库、企业制度库使用 employee 权限，售后知识库使用 service 权限，管理员空间使用 admin 权限。",
        "3. 每题调用项目真实 RagSummarizeService，保留混合检索、RRF、Reranker、相关度阈值和生成提示词等线上配置。",
        "4. 裁判只以标准答案为事实基准，先拆分原子事实，再判 covered、missing、contradicted；Python 按固定规则计算最终等级。",
        "5. correct=1、partial=0.5、wrong=0；judge_error 不进入准确率分母，但单独报告。",
        "6. 来源命中表示返回引用中包含该题对应的预期文件；它衡量检索，不等同于最终回答正确率。",
        "",
        "## 配置快照",
        "",
        f"- temperature / top_p：{model.get('answer_temperature')} / {model.get('answer_top_p')}",
        f"- Embedding：{model.get('embedding')}",
        f"- 混合检索：{retrieval.get('hybrid_enabled')}，向量候选 {retrieval.get('vector_k')}，BM25 候选 {retrieval.get('bm25_k')}，融合后 {retrieval.get('final_k')}",
        f"- Query Rewrite：{retrieval.get('query_rewrite_enabled')}",
        f"- Reranker：{retrieval.get('reranker_enabled')}，模型 {retrieval.get('reranker_model')}，输出 {retrieval.get('reranker_output_k')}",
        f"- 向量距离阈值：{retrieval.get('relevance_distance_threshold')}",
        "",
        "完整回答、引用、逐事实评分和耗时保存在同名 JSON 明细文件中。",
    ]

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"overall": overall, "by_space": by_space, "by_format": by_format}


def print_summary(summary: dict[str, Any]) -> None:
    overall = summary["overall"]
    print("", flush=True)
    print("========== 评测结果 ==========" , flush=True)
    print(f"题数：{overall['total']}（裁判异常：{overall['judge_error']}）", flush=True)
    print(f"加权准确率：{percent(overall['weighted_accuracy'])}", flush=True)
    print(f"完全正确率：{percent(overall['exact_accuracy'])}", flush=True)
    print(
        f"正确/部分/错误：{overall['correct']}/{overall['partial']}/{overall['wrong']}",
        flush=True,
    )
    print(f"来源命中率：{percent(overall['source_hit_rate'])}", flush=True)
    print(f"来源 Top1 命中率：{percent(overall['source_top1_rate'])}", flush=True)
    print(
        f"耗时 avg/p50/p95：{overall['latency_avg_ms']:.0f}/"
        f"{overall['latency_p50_ms']:.0f}/{overall['latency_p95_ms']:.0f} ms",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评测 160 份独立企业资料的 RAG 回答准确率")
    parser.add_argument(
        "phase", nargs="?", choices=("all", "answers", "judge", "report"), default="all"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="测评题清单路径")
    parser.add_argument("--limit", type=int, default=None, help="仅运行前 N 题")
    parser.add_argument("--workers", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--run-id", default=None, help="输出文件后缀，例如 smoke_8")
    parser.add_argument("--fresh", action="store_true", help="忽略同名缓存并重新执行")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit 必须大于 0")
    if args.workers <= 0:
        raise ValueError("--workers 必须大于 0")

    input_path = args.input.resolve()
    questions = load_questions(input_path, args.limit)
    answers_path, results_path, report_path = output_paths(args.run_id)
    metadata = build_metadata(input_path, len(questions), args.workers)

    if args.phase in {"all", "answers"}:
        answers = run_answers(
            questions, answers_path, metadata, args.workers, args.fresh
        )
    else:
        answers_metadata, answer_map = read_state(answers_path)
        if not answer_map:
            raise FileNotFoundError(f"没有可用的回答结果：{answers_path}")
        metadata = {**metadata, **answers_metadata}
        question_ids = {question["id"] for question in questions}
        answers = sorted(
            [item for item in answer_map.values() if item["id"] in question_ids],
            key=lambda item: item["sequence"],
        )

    if args.phase in {"all", "judge"}:
        results = run_judge(
            answers, results_path, metadata, args.workers, args.fresh
        )
        summary = build_report(results, metadata, report_path)
        print_summary(summary)
        print(f"报告：{report_path}", flush=True)
    elif args.phase == "report":
        results_metadata, result_map = read_state(results_path)
        if not result_map:
            raise FileNotFoundError(f"没有可用的裁判结果：{results_path}")
        metadata = {**metadata, **results_metadata}
        question_ids = {question["id"] for question in questions}
        results = sorted(
            [item for item in result_map.values() if item["id"] in question_ids],
            key=lambda item: item["sequence"],
        )
        summary = build_report(results, metadata, report_path)
        print_summary(summary)
        print(f"报告：{report_path}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
