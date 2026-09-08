"""
可信RAG校准问题集
用于评测RAG的准确性、拒答能力和引用质量
"""

# 校准问题定义
CALIBRATION_QUESTIONS = [
    # ===== 类别1：知识库内有明确答案（8条） =====
    {
        "id": "Q001",
        "question": "小户型适合哪些扫地机器人？",
        "category": "有明确答案",
        "expected_behavior": "应基于产品知识库回答，给出具体产品型号和理由",
        "expected_answer_contains": ["扫地机器人", "小户型"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q002",
        "question": "公司的扫地机器人有哪些型号？",
        "category": "有明确答案",
        "expected_behavior": "应列出知识库中的产品型号",
        "expected_answer_contains": ["型号"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q003",
        "question": "扫地机器人的价格是多少？",
        "category": "有明确答案",
        "expected_behavior": "应给出具体价格信息",
        "expected_answer_contains": ["价格", "元"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q004",
        "question": "产品的保修期是多久？",
        "category": "有明确答案",
        "expected_behavior": "应给出保修政策",
        "expected_answer_contains": ["保修"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q005",
        "question": "扫地机器人支持哪些清扫模式？",
        "category": "有明确答案",
        "expected_behavior": "应列出清扫模式",
        "expected_answer_contains": ["模式"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q006",
        "question": "产品的续航时间是多长？",
        "category": "有明确答案",
        "expected_behavior": "应给出续航信息",
        "expected_answer_contains": ["续航"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q007",
        "question": "扫地机器人的噪音水平如何？",
        "category": "有明确答案",
        "expected_behavior": "应给出噪音相关信息",
        "expected_answer_contains": ["噪音"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q008",
        "question": "如何充电和维护扫地机器人？",
        "category": "有明确答案",
        "expected_behavior": "应给出充电和维护说明",
        "expected_answer_contains": ["充电"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },

    # ===== 类别2：需要多条资料回答（4条） =====
    {
        "id": "Q009",
        "question": "对比不同型号的扫地机器人，哪个性价比最高？",
        "category": "需要多条资料",
        "expected_behavior": "应综合多个产品信息进行对比",
        "expected_answer_contains": ["对比", "性价比"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q010",
        "question": "产品的售后服务包括哪些内容？",
        "category": "需要多条资料",
        "expected_behavior": "应综合售后政策和服务内容",
        "expected_answer_contains": ["售后"],
        "should_refuse": False,
        "accessible_roles": ["service", "admin"]  # 仅客服和管理员可见
    },
    {
        "id": "Q011",
        "question": "所有扫地机器人产品的共同特点是什么？",
        "category": "需要多条资料",
        "expected_behavior": "应提取多个产品的共性特征",
        "expected_answer_contains": ["特点"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q012",
        "question": "如何根据房屋面积选择合适的扫地机器人型号？",
        "category": "需要多条资料",
        "expected_behavior": "应综合面积和产品适用范围",
        "expected_answer_contains": ["面积", "型号"],
        "should_refuse": False,
        "accessible_roles": ["employee", "service", "admin"]
    },

    # ===== 类别3：知识库中没有答案（4条） =====
    {
        "id": "Q013",
        "question": "公司明年会推出什么新产品？",
        "category": "知识库无答案",
        "expected_behavior": "应拒答，说明知识库中没有相关信息",
        "expected_answer_contains": ["没有", "无法", "不足"],
        "should_refuse": True,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q014",
        "question": "CEO的办公室在哪里？",
        "category": "知识库无答案",
        "expected_behavior": "应拒答",
        "expected_answer_contains": ["没有", "无法", "不足"],
        "should_refuse": True,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q015",
        "question": "公司的股票代码是什么？",
        "category": "知识库无答案",
        "expected_behavior": "应拒答",
        "expected_answer_contains": ["没有", "无法", "不足"],
        "should_refuse": True,
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q016",
        "question": "如何投资比特币？",
        "category": "知识库无答案",
        "expected_behavior": "应拒答，不回答知识库外的问题",
        "expected_answer_contains": ["没有", "无法", "不足"],
        "should_refuse": True,
        "accessible_roles": ["employee", "service", "admin"]
    },

    # ===== 类别4：权限问题（2条） =====
    {
        "id": "Q017",
        "question": "客户投诉后如何处理退款？",
        "category": "权限受限",
        "expected_behavior": "普通员工无权访问售后资料，应拒答或返回空结果",
        "expected_answer_contains": ["没有", "无法", "不足"],
        "should_refuse": True,
        "accessible_roles": ["service", "admin"]  # 仅客服和管理员
    },
    {
        "id": "Q018",
        "question": "售后维修的流程是什么？",
        "category": "权限受限",
        "expected_behavior": "普通员工无权访问，应拒答",
        "expected_answer_contains": ["没有", "无法", "不足"],
        "should_refuse": True,
        "accessible_roles": ["service", "admin"]  # 仅客服和管理员
    },

    # ===== 类别5：文档状态问题（2条） =====
    {
        "id": "Q019",
        "question": "已下架产品的信息应该不可见",
        "category": "文档状态",
        "expected_behavior": "已下架或删除的文档不应出现在引用中",
        "note": "需要实际下架某个文档后测试",
        "should_refuse": None,  # 取决于是否有其他可用文档
        "accessible_roles": ["employee", "service", "admin"]
    },
    {
        "id": "Q020",
        "question": "旧版本文档不应被引用",
        "category": "文档状态",
        "expected_behavior": "引用中只应出现当前版本，不应出现旧版本",
        "note": "需要上传新版本后测试",
        "should_refuse": None,
        "accessible_roles": ["employee", "service", "admin"]
    }
]


def get_questions_by_category(category: str):
    """按类别获取问题"""
    return [q for q in CALIBRATION_QUESTIONS if q["category"] == category]


def get_questions_for_role(role: str):
    """获取指定角色可访问的问题"""
    return [q for q in CALIBRATION_QUESTIONS if role in q["accessible_roles"]]


def print_calibration_summary():
    """打印校准问题集概览"""
    print("=" * 60)
    print("可信RAG校准问题集")
    print("=" * 60)

    categories = {}
    for q in CALIBRATION_QUESTIONS:
        cat = q["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(q)

    for cat, questions in categories.items():
        print(f"\n【{cat}】 {len(questions)}条")
        for q in questions:
            print(f"  {q['id']}: {q['question']}")

    print(f"\n总计：{len(CALIBRATION_QUESTIONS)}条问题")
    print("=" * 60)


if __name__ == '__main__':
    print_calibration_summary()

    # 示例：获取员工角色的问题
    print("\n\n普通员工可测试的问题：")
    employee_questions = get_questions_for_role("employee")
    for q in employee_questions:
        print(f"  {q['id']}: {q['question']}")
