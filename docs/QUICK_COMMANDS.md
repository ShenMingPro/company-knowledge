# 生成稳定性与裁判稳定性优化 - 快速命令参考

## 🧪 测试命令

### 运行所有新增测试（32个）
```bash
python -m pytest tests/test_rubric.py tests/test_model_factory.py tests/test_eval_integration.py -v
```

### 运行回归测试（34个）
```bash
python -m pytest tests/test_rag_trusted.py tests/test_hybrid_retrieval.py -v
```

### 运行完整测试套件（66个）
```bash
python -m pytest tests/ -v
```

### 运行特定测试
```bash
# emp_06 测试
python -m pytest tests/test_rubric.py::TestCalculateVerdict::test_emp_06_partial_coverage_returns_partial -v

# 裁判集成测试
python -m pytest tests/test_eval_integration.py::TestJudgeOneIntegration -v

# 模型工厂测试
python -m pytest tests/test_model_factory.py -v
```

---

## 🔍 验证命令

### 验证模型配置
```bash
python -c "
from utils.config_handler import rag_conf
print('chat_model_temperature:', rag_conf.get('chat_model_temperature'))
print('chat_model_top_p:', rag_conf.get('chat_model_top_p'))
print('judge_model_temperature:', rag_conf.get('judge_model_temperature'))
print('judge_model_top_p:', rag_conf.get('judge_model_top_p'))
"
```

### 验证 Rubric 判分逻辑
```bash
python -c "
from evaluation.rubric import calculate_verdict

# emp_06 场景：3/11 覆盖 -> partial
kp_result = [{'status':'covered','evidence':f'证据{i+1}','key_point':f'要点{i+1}'} for i in range(3)]
kp_result += [{'status':'missing','evidence':'','key_point':f'要点{i+1}'} for i in range(3,11)]
result = calculate_verdict(kp_result)
print(f'emp_06 (3/11): {result} (预期: partial)')
assert result == 'partial', f'Expected partial, got {result}'
print('验证通过')
"
```

### 验证 emp_06 关键要点
```bash
python -c "
import json
with open('evaluation/golden_set.json', 'r', encoding='utf-8') as f:
    golden_set = json.load(f)
emp_06 = next(q for q in golden_set if q['id'] == 'emp_06')
print(f'emp_06 关键要点数量: {len(emp_06.get(\"key_points\", []))}')
for i, kp in enumerate(emp_06.get('key_points', []), 1):
    print(f'  {i}. {kp}')
"
```

### 验证裁判提示词格式化
```bash
python -c "
template = open('evaluation/judge_prompt.txt', 'r', encoding='utf-8').read()
result = template.format(question='测试', key_points='要点', model_answer='回答')
print('格式化成功，长度:', len(result))
"
```

---

## 📊 评测命令

### 运行新评测流程
```bash
cd evaluation
python run_eval_v2.py
```

### 限制评测数量
```bash
cd evaluation
python run_eval_v2.py --limit 10
```

### 生成评测报告
```bash
cd evaluation
python run_eval_v2.py --report
```

### 查看评测结果
```bash
# 查看详细结果
cat evaluation/results_v2.json

# 查看报告
cat evaluation/report_v2.md

# 查看缓存的答案
cat evaluation/answers.json
```

---

## 🔬 稳定性测试（可选，需要 API Key）

### 运行完整稳定性测试
```bash
cd evaluation
python test_stability.py
```

### 只测试生成稳定性
```python
from evaluation.test_stability import test_generation_stability
test_generation_stability(
    question="想离职的话需要提前多久提？忘记了怎么办？",
    n_runs=5
)
```

### 只测试裁判稳定性
```python
from evaluation.test_stability import test_judge_stability
test_judge_stability(
    question="想离职的话需要提前多久提？忘记了怎么办？",
    answer="正式员工离职需提前30天书面通知，试用期员工提前3天通知。需要在OA系统提交离职申请。",
    n_runs=5
)
```

---

## 🛠️ 配置修改

### 调整生成稳定性
编辑 `config/rag.yml`：
```yaml
chat_model_temperature: 0.0  # [0.0-2.0]，越低越稳定
chat_model_top_p: 1.0        # [0.0-1.0]，1.0 禁用随机截断
```

### 调整裁判稳定性
编辑 `config/rag.yml`：
```yaml
judge_model_temperature: 0.0
judge_model_top_p: 1.0
```

### 调整判分阈值
编辑 `evaluation/rubric.py`：
```python
def calculate_verdict(
    key_points_result: List[Dict[str, any]],
    min_partial_coverage: float = 0.0  # 修改这个值
) -> Literal["correct", "partial", "wrong"]:
    ...
```

---

## 📝 代码检查

### 检查 API Key 不泄露
```bash
python -m pytest tests/test_model_factory.py::TestModelFactoryStability::test_api_key_not_in_model_metadata -v
python -m pytest tests/test_eval_integration.py::TestModelMetadata::test_get_model_metadata_no_api_key -v
```

### 检查模型工厂
```bash
python -c "
from model.factory import create_chat_model

# answer 模型
answer_model = create_chat_model(purpose='answer')
print('Answer model kwargs:', answer_model.model_kwargs)

# judge 模型
judge_model = create_chat_model(purpose='judge')
print('Judge model kwargs:', judge_model.model_kwargs)
"
```

---

## 🚨 故障排查

### 测试失败
```bash
# 查看详细错误
python -m pytest tests/test_rubric.py -v --tb=long

# 只运行失败的测试
python -m pytest tests/test_rubric.py --lf -v
```

### 配置问题
```bash
# 检查配置文件是否正确加载
python -c "
from utils.config_handler import rag_conf
import json
print(json.dumps({
    'chat_model_temperature': rag_conf.get('chat_model_temperature'),
    'chat_model_top_p': rag_conf.get('chat_model_top_p'),
    'judge_model_temperature': rag_conf.get('judge_model_temperature'),
    'judge_model_top_p': rag_conf.get('judge_model_top_p')
}, indent=2))
"
```

### JSON 解析错误
```bash
# 验证 golden_set.json 格式
python -c "
import json
with open('evaluation/golden_set.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
print(f'加载成功，共 {len(data)} 道题')
"
```

### 裁判提示词问题
```bash
# 验证模板格式
python -c "
template = open('evaluation/judge_prompt.txt', 'r', encoding='utf-8').read()
# 检查是否有未转义的花括号
import re
unescaped = re.findall(r'(?<!\{)\{(?!\{)([^{}]+)(?<!\})\}(?!\})', template)
print('需要格式化的变量:', unescaped)
"
```

---

## 📚 查看文档

### 实施总结
```bash
cat docs/implementation_summary_generation_stability.md
```

### 验证清单
```bash
cat docs/VERIFICATION_CHECKLIST.md
```

### 最终报告
```bash
cat docs/FINAL_REPORT_GENERATION_STABILITY.md
```

---

## ✅ 快速验收（一键运行）

```bash
# Windows
cd "D:\python项目\AI大模型RAG与智能体开发_Agent项目"

# 1. 验证配置
python -c "from utils.config_handler import rag_conf; assert rag_conf.get('chat_model_temperature') == 0.0; assert rag_conf.get('judge_model_temperature') == 0.0; print('配置验证通过')"

# 2. 验证 Rubric
python -c "from evaluation.rubric import calculate_verdict; kp=[{'status':'covered','evidence':'x','key_point':'1'}]*3+[{'status':'missing','evidence':'','key_point':'1'}]*8; assert calculate_verdict(kp)=='partial'; print('Rubric 验证通过')"

# 3. 运行所有测试
python -m pytest tests/test_rubric.py tests/test_model_factory.py tests/test_eval_integration.py tests/test_rag_trusted.py tests/test_hybrid_retrieval.py -v

# 预期：66 passed
```

---

**就绪！所有命令均已验证可用。** ✨
