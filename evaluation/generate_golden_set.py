"""
评测集生成脚本：基于知识库真实文档自动生成三角色问答测试案例

graph 流程：
    查询数据库 → 按角色取对应空间的知识文档（current_version，按名称去重）
        → 逐份文档提取全文（复用 file_handler 的 docx_loader）
        → LLM 出题（每份文档 2 题：question + reference_answer + key_points）
              出题要求：答案逐字基于原文事实（数字/期限/比例/流程），禁止编造
        → 按角色归组、每角色截取前 20 题（emp_01~20 / cs_01~20 / admin_01~20）
        → 写入 evaluation/golden_set.json（与 run_eval.py / run_eval_v2.py 兼容）

用法：
    python evaluation/generate_golden_set.py          # 全量生成（3角色）
    python evaluation/generate_golden_set.py --role 员工   # 只生成某角色
"""
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# 保证可从项目根目录导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import get_session, KnowledgeSpace, Document, DocumentVersion
from utils.file_handler import docx_loader
from model.factory import create_chat_model

# ==================== 路径与配置 ====================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_PATH = os.path.join(BASE_DIR, "evaluation", "golden_set.json")

MAX_WORKERS = 3            # LLM 并发数（DashScope 有 QPM 限制）
QUESTIONS_PER_DOC = 2      # 每份文档出题数
TARGET_PER_ROLE = 20       # 每个角色的目标题数
MAX_DOC_CHARS = 8000       # 喂给 LLM 的文档最大字符数（防止超长）

# 写文件锁（多线程落盘用）
_lock = threading.Lock()

# 角色 → 出题文档配置
# include 为文档名前缀白名单；None 表示取空间内全部 docx（去重后）
ROLE_DOC_CONFIG = [
    {
        "role": "员工",
        "role_code": "employee",
        "id_prefix": "emp",
        "space_code": "policy",
        # 企业制度库中的制度类文档（排除误传的产品文档）
        "include": ["01-公司简介", "02-组织架构", "03-员工手册", "04-考勤管理制度",
                    "05-薪酬福利", "06-绩效考核", "07-招聘与入职", "08-培训与晋升",
                    "09-差旅", "10-财务管理", "11-采购管理", "12-保密与信息安全",
                    "13-办公设备", "14-行政管理与会议", "15-企业文化"],
    },
    {
        "role": "客服",
        "role_code": "service",
        "id_prefix": "cs",
        "space_code": "service",
        "include": None,   # 售后知识库全部文档
    },
    {
        "role": "管理员",
        "role_code": "admin",
        "id_prefix": "admin",
        "space_code": "product",
        # 产品知识库中的产品/技术文档（排除误传的售后文档与 txt 测试文档）
        "include": ["01-云智协同办公平台", "02-云智协同PC端", "03-云智协同移动端",
                    "04-云智协同管理员", "05-云智知识库平台", "06-云智智能客服系统",
                    "07-云智视频会议", "08-云智协同V5.2", "09-产品常见问题FAQ",
                    "10-开放平台API", "11-产品安全与隐私", "12-产品线介绍",
                    "13-系统部署与运维", "14-产品术语表"],
    },
]

# 出题提示词：让 LLM 基于文档事实出题，标准答案必须忠于原文
GEN_PROMPT = """你是企业知识库问答评测集的出题专家。请基于下面的【文档内容】，为"{role_name}"角色出 {n} 道问答测试题。

出题要求：
1. 问题必须是文档内容能明确回答的，禁止编造文档中不存在的信息；
2. 标准答案必须逐字基于原文事实（数字、期限、金额、比例、流程步骤、条件等），不得扩写或编造；
3. 每题给出 4~8 个关键要点（key_points），每个要点是一条独立、可判定对错的事实；
4. 问题要像真实用户提问，口语化，可以包含 1~2 个子问题（如"A？B？"）；
5. 针对具体规则/数字/流程出题，避免"介绍一下XX"这类宽泛问题；
6. 不同题目考查文档的不同章节，不要集中在同一处内容。

【文档名称】{doc_name}
【文档内容】
{content}

请只输出如下 JSON 数组，不要输出任何其他内容：
[{{"question": "问题", "reference_answer": "标准答案", "key_points": ["要点1", "要点2", "..."]}}]"""


def get_role_documents(config):
    """
    从数据库查询某角色对应空间的知识文档（current_version，按文档名去重）
    :param config: ROLE_DOC_CONFIG 中的一项
    :return: list[dict]，每项含 name/file_path
    """
    session = get_session()
    try:
        space = session.query(KnowledgeSpace).filter_by(code=config["space_code"]).first()
        if not space:
            raise ValueError(f"未找到知识空间 code={config['space_code']}")

        docs = (session.query(Document, DocumentVersion)
                .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
                .filter(Document.space_id == space.id)
                .all())

        # 按文档名去重（同名文档只保留一份），并按白名单过滤
        seen, result = set(), []
        for doc, ver in docs:
            if not doc.name.endswith(".docx"):
                continue
            if config["include"] is not None:
                if not any(doc.name.startswith(p) for p in config["include"]):
                    continue
            if doc.name in seen:
                continue
            seen.add(doc.name)
            result.append({"name": doc.name, "file_path": os.path.join(BASE_DIR, ver.file_path.replace("\\", "/"))})
        return result
    finally:
        session.close()


def extract_text(file_path):
    """
    提取 docx 文档全文（复用项目 docx_loader），超长时截断
    :param file_path: 文档绝对路径
    :return: 文本字符串（最多 MAX_DOC_CHARS 字），提取失败返回空串
    """
    try:
        docs = docx_loader(file_path)
        text = "\n".join(d.page_content for d in docs).strip()
        if len(text) > MAX_DOC_CHARS:
            # 前 6000 + 后 2000：保留文档开头总述与结尾附录类要点
            text = text[:6000] + "\n......\n" + text[-2000:]
        return text
    except Exception as e:
        print(f"  [跳过] 文档提取失败 {os.path.basename(file_path)}: {e}")
        return ""


def generate_questions_for_doc(chat_model, role_name, doc_name, content):
    """
    调用 LLM 为单份文档出题
    :return: list[dict]，每题含 question/reference_answer/key_points；失败返回空列表
    """
    prompt = GEN_PROMPT.format(role_name=role_name, n=QUESTIONS_PER_DOC,
                               doc_name=doc_name, content=content)
    for attempt in range(2):  # 解析失败重试一次
        try:
            resp = chat_model.invoke(prompt)
            text = resp.content.strip()
            start, end = text.find("["), text.rfind("]")
            if start == -1 or end == -1:
                raise ValueError(f"输出无法解析: {text[:80]}")
            items = json.loads(text[start:end + 1])
            # 只保留字段完整的题目
            return [it for it in items
                    if it.get("question") and it.get("reference_answer") and it.get("key_points")]
        except Exception as e:
            if attempt == 1:
                print(f"  [失败] {doc_name} 出题失败: {e}")
    return []


def generate_for_role(chat_model, config):
    """
    为单个角色生成测试案例：查文档 → 并发出题 → 截取前 TARGET_PER_ROLE 题
    :param config: ROLE_DOC_CONFIG 中的一项
    :return: (role_block dict, 成功文档数)
    """
    docs = get_role_documents(config)
    print(f"\n[{config['role']}] 空间 {config['space_code']}，待出题文档 {len(docs)} 份")

    all_questions = []
    done = [0]

    def worker(doc):
        """单文档任务：提取文本 → LLM 出题"""
        content = extract_text(doc["file_path"])
        if len(content) < 200:  # 内容过短无法出题
            return doc["name"], []
        qs = generate_questions_for_doc(chat_model, config["role"], doc["name"], content)
        return doc["name"], qs

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(worker, doc) for doc in docs]
        for fut in as_completed(futures):
            doc_name, qs = fut.result()
            all_questions.extend(qs)
            done[0] += 1
            print(f"  ({done[0]}/{len(docs)}) {doc_name} 出题 {len(qs)} 道")

    # 截取目标题数并编号
    questions = []
    for i, q in enumerate(all_questions[:TARGET_PER_ROLE], 1):
        questions.append({
            "id": f"{config['id_prefix']}_{i:02d}",
            "question": q["question"],
            "reference_answer": q["reference_answer"],
            "key_points": q["key_points"],
            "source": "知识库文档",
        })

    block = {
        "role": config["role"],
        "role_code": config["role_code"],
        "questions": questions,
    }
    print(f"[{config['role']}] 共出题 {len(all_questions)} 道，取前 {len(questions)} 道")
    return block, len(docs)


def main():
    """主流程：逐角色出题 → 汇总写盘"""
    only_role = sys.argv[sys.argv.index("--role") + 1] if "--role" in sys.argv else None
    configs = [c for c in ROLE_DOC_CONFIG if not only_role or c["role"] == only_role]

    chat_model = create_chat_model()  # 与问答系统同款模型
    blocks = []
    for config in configs:
        block, _ = generate_for_role(chat_model, config)
        blocks.append(block)

    # 合并模式：保留评测集中其他角色的已有题目，仅替换本次生成的角色块
    generated_roles = {b["role"] for b in blocks}
    if os.path.exists(GOLDEN_PATH):
        with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
            old = json.load(f)
        for b in old.get("roles", []):
            if b["role"] not in generated_roles:
                blocks.append(b)

    data = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "description": "KnowBase Lite 三角色评测集（基于知识库文档自动生成）",
        },
        "roles": blocks,
    }
    with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = sum(len(b["questions"]) for b in blocks)
    print(f"\n[完成] 新评测集已写入 {GOLDEN_PATH}，共 {total} 题")


if __name__ == "__main__":
    main()
