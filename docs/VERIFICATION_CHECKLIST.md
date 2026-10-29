# 生成稳定性与裁判稳定性优化 - 验证清单

## ✅ 已完成的工作

### 一、配置与基础设施

- [x] `config/rag.yml` - 新增 chat_model_temperature, chat_model_top_p
- [x] `config/rag.yml` - 新增 judge_model_temperature, judge_model_top_p
- [x] `model/factory.py` - 新增 create_chat_model(purpose="answer/judge")
- [x] `model/factory.py` - 保留 chat_model 兼容别名
- [x] `model/factory.py` - 通过 model_kwargs 传递 temperature

### 二、生成优化

- [x] `prompts/rag_summarize.txt` - 新增回答策略章节
- [x] `prompts/rag_summarize.txt` - 要求拆解多子问题
- [x] `prompts/rag_summarize.txt` - 要求完整回答流程类问题
- [x] `prompts/rag_summarize.txt` - 要求覆盖所有关键信息
- [x] `prompts/rag_summarize.txt` - 部分缺失时继续回答

### 三、裁判优化

- [x] `evaluation/rubric.py` - Python 判分规则
- [x] `evaluation/rubric.py` - calculate_verdict() 函数
- [x] `evaluation/rubric.py` - calculate_coverage_stats() 函数
- [x] `evaluation/rubric.py` - validate_key_points_result() 函数
- [x] `evaluation/judge_prompt.txt` - 裁判提示词（逐要点判断）
- [x] `evaluation/run_eval_v2.py` - 新评测流程
- [x] `evaluation/run_eval_v2.py` - judge_one() 函数
- [x] `evaluation/run_eval_v2.py` - get_model_metadata() 函数
- [x] `evaluation/golden_set.json` - 为 emp_06 等题目添加 key_points

### 四、测试覆盖

- [x] `tests/test_rubric.py` - 判分规则单元测试（15个）
- [x] `tests/test_model_factory.py` - 模型工厂测试（7个）
- [x] `tests/test_eval_integration.py` - 评测集成测试（10个）
- [x] 所有新增测试通过（32/32）
- [x] 现有功能不回归（34/34）

### 五、文档交付

- [x] `docs/implementation_summary_generation_stability.md` - 完整实施总结
- [x] `evaluation/test_stability.py` - 稳定性验证脚本
- [x] `docs/VERIFICATION_CHECKLIST.md` - 本验证清单

---

## 🧪 验收测试命令

### 1. 运行所有单元测试

```bash
# 新增测试（32个）
python -m pytest tests/test_rubric.py tests/test_model_factory.py tests/test_eval_integration.py -v

# 现有功能不回归（34个）
python -m pytest tests/test_rag_trusted.py tests/test_hybrid_retrieval.py -v

# 完整测试套件（66个）
python -m pytest tests/ -v
```

**预期结果**：66 passed ✅

### 2. 验证模型配置

```bash
# 检查配置文件
python -c "
from utils.config_handler import rag_conf
print('chat_model_temperature:', rag_conf.get('chat_model_temperature'))
print('chat_model_top_p:', rag_conf.get('chat_model_top_p'))
print('judge_model_temperature:', rag_conf.get('judge_model_temperature'))
print('judge_model_top_p:', rag_conf.get('judge_model_top_p'))
"
```

**预期输出**：
```
chat_model_temperature: 0.0
chat_model_top_p: 1.0
judge_model_temperature: 0.0
judge_model_top_p: 1.0
```

### 3. 验证模型工厂

```bash
# 检查 answer 模型参数
python -c "
from model.factory import create_chat_model
model = create_chat_model(purpose='answer')
print('Model kwargs:', model.model_kwargs)
"

# 检查 judge 模型参数
python -c "
from model.factory import create_chat_model
model = create_chat_model(purpose='judge')
print('Model kwargs:', model.model_kwargs)
"
```

**预期输出**：
```
Model kwargs: {'temperature': 0.0, 'top_p': 1.0}
```

### 4. 验证裁判提示词格式化

```bash
python -c "
template = open('evaluation/judge_prompt.txt', 'r', encoding='utf-8').read()
result = template.format(question='测试问题', key_points='要点1', model_answer='回答')
print('格式化成功，长度:', len(result))
"
```

**预期输出**：`格式化成功，长度: XXXX`

### 5. 验证 rubric 判分逻辑

```bash
python -c "
from evaluation.rubric import calculate_verdict

# 全覆盖 -> correct
result = calculate_verdict([
    {'status': 'covered', 'evidence': '证据1'},
    {'status': 'covered', 'evidence': '证据2'}
])
assert result == 'correct', f'Expected correct, got {result}'

# 部分覆盖 -> partial
result = calculate_verdict([
    {'status': 'covered', 'evidence': '证据1'},
    {'status': 'missing', 'evidence': ''}
])
assert result == 'partial', f'Expected partial, got {result}'

# 零覆盖 -> wrong
result = calculate_verdict([
    {'status': 'missing', 'evidence': ''},
    {'status': 'missing', 'evidence': ''}
])
assert result == 'wrong', f'Expected wrong, got {result}'

# 有矛盾 -> wrong
result = calculate_verdict([
    {'status': 'covered', 'evidence': '证据1'},
    {'status': 'contradicted', 'evidence': '矛盾'}
])
assert result == 'wrong', f'Expected wrong, got {result}'

print('✅ Rubric 判分逻辑验证通过')
"
```

**预期输出**：`✅ Rubric 判分逻辑验证通过`

### 6. 验证 emp_06 关键要点

```bash
python -c "
import json
with open('evaluation/golden_set.json', 'r', encoding='utf-8') as f:
    golden_set = json.load(f)

emp_06 = next(q for q in golden_set if q['id'] == 'emp_06')
key_points = emp_06.get('key_points', [])

print(f'emp_06 关键要点数量: {len(key_points)}')
print('关键要点:')
for i, kp in enumerate(key_points, 1):
    print(f'  {i}. {kp}')
"
```

**预期输出**：显示 emp_06 的 11 个关键要点

---

## 🎯 核心验收标准

### 标准 1：模型配置生效

**检查点**：
- [ ] chat_model_temperature = 0.0
- [ ] chat_model_top_p = 1.0
- [ ] judge_model_temperature = 0.0
- [ ] judge_model_top_p = 1.0
- [ ] create_chat_model(purpose="answer") 使用 answer 配置
- [ ] create_chat_model(purpose="judge") 使用 judge 配置

**验证方法**：运行"验收测试命令 2 和 3"

### 标准 2：emp_06 稳定判为 partial

**检查点**：
- [ ] emp_06 有 11 个关键要点
- [ ] 只覆盖 3 个要点（30天/3天/OA）时判为 partial
- [ ] 测试用例 `test_emp_06_partial_coverage` 通过

**验证方法**：
```bash
python -m pytest tests/test_rubric.py::TestCalculateVerdict::test_emp_06_partial_coverage_returns_partial -v
```

### 标准 3：裁判稳定性

**检查点**：
- [ ] 最终 verdict 由 Python rubric 计算，不直接信任模型
- [ ] JSON 解析失败标记为 judge_error，与 wrong 分开
- [ ] covered 但无证据自动降级为 missing

**验证方法**：
```bash
python -m pytest tests/test_eval_integration.py::TestJudgeOneIntegration -v
```

### 标准 4：现有功能不回归

**检查点**：
- [ ] test_rag_trusted.py 全部通过（14个）
- [ ] test_hybrid_retrieval.py 全部通过（20个）

**验证方法**：运行"验收测试命令 1"

### 标准 5：API Key 不泄露

**检查点**：
- [ ] get_model_metadata() 不返回 API Key
- [ ] 模型实例不暴露 API Key

**验证方法**：
```bash
python -m pytest tests/test_model_factory.py::TestModelFactoryStability::test_api_key_not_in_model_metadata -v
python -m pytest tests/test_eval_integration.py::TestModelMetadata::test_get_model_metadata_no_api_key -v
```

---

## 🔬 可选：生成稳定性实战测试

### 测试方法

```bash
cd evaluation
python test_stability.py
```

### 预期行为

**生成稳定性测试**：
- 同一问题连续生成 5 次
- 关键事实覆盖集合至少 4/5 一致（≥80% 稳定率）
- 文本可能存在微小差异（预期行为）

**裁判稳定性测试**：
- 同一答案连续裁判 5 次
- verdict 和 score 应完全一致
- coverage_rate 应保持稳定

**注意**：
- 此测试需要真实 API Key 和联网
- 会消耗少量 API 调用额度
- 如果 API 不可用，跳过此测试不影响验收

---

## ⚠️ 已知限制与风险

### 技术限制

1. **temperature=0 非绝对确定**
   - ChatTongyi 不支持 seed 参数
   - 验收应比较要点覆盖，而非字节级文本

2. **golden_set.json 需人工维护**
   - 当前只有部分题目有 key_points
   - 未添加的题目使用传统评测方式

3. **检索质量影响**
   - 如果关键内容未进入检索上下文，是检索问题
   - 不要把检索问题误判为生成随机性问题

### 需人工确认

1. **检查 emp_06 的检索上下文**
   - 确认"文档移交、权限回收"等内容是否被检索到
   - 如果未检索到，需优化检索模块，而非生成模块

2. **评测基线重建**
   - 建议重新运行完整评测，建立新基线
   - 旧评测结果与新评测结果不完全可比

3. **判分阈值调优**
   - 当前规则：至少 1 个要点覆盖 -> partial
   - 可根据实际需求调整 rubric.py

---

## 📊 测试结果汇总

### 单元测试

| 测试文件 | 测试数量 | 状态 |
|---------|---------|------|
| tests/test_rubric.py | 15 | ✅ PASS |
| tests/test_model_factory.py | 7 | ✅ PASS |
| tests/test_eval_integration.py | 10 | ✅ PASS |
| **新增测试小计** | **32** | **✅ PASS** |

### 回归测试

| 测试文件 | 测试数量 | 状态 |
|---------|---------|------|
| tests/test_rag_trusted.py | 14 | ✅ PASS |
| tests/test_hybrid_retrieval.py | 20 | ✅ PASS |
| **现有功能小计** | **34** | **✅ PASS** |

### 总计

**66 个测试全部通过 ✅**

---

## 🚀 后续工作建议

### 短期（1-2周）

1. 为 golden_set.json 的所有题目补全 key_points
2. 运行完整评测，建立新基线
3. 分析高频 missing 要点，优化检索或生成

### 中期（1个月）

1. 检索质量分析：识别检索问题 vs 生成问题
2. 判分规则调优：根据实际结果调整阈值
3. 评测报告增强：要点覆盖热力图、稳定性分析

### 长期（2-3个月）

1. 要点覆盖监控：在生产环境记录覆盖率
2. A/B 测试框架：对比不同配置的效果
3. 自动化评测：集成到 CI/CD 流程

---

## ✅ 最终确认

- [x] 所有配置文件已更新
- [x] 所有代码文件已实现
- [x] 所有测试文件已创建
- [x] 66 个测试全部通过
- [x] 现有功能不回归
- [x] 文档完整交付
- [x] 验证清单完成

**功能已全部完成并测试通过，可以投入使用！** 🎉

---

## 📝 快速开始

```bash
# 1. 验证所有测试通过
python -m pytest tests/test_rubric.py tests/test_model_factory.py tests/test_eval_integration.py -v

# 2. 验证现有功能不回归
python -m pytest tests/test_rag_trusted.py tests/test_hybrid_retrieval.py -v

# 3. 运行新评测流程
cd evaluation
python run_eval_v2.py --limit 10

# 4. (可选) 运行稳定性实战测试
python test_stability.py
```

**就绪！** ✨
