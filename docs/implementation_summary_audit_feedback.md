# 审计日志、问答持久化与用户反馈实施总结

## 1. 修改和新增文件

### 新增服务层文件
- **services/interaction_service.py** - 问答持久化服务
- **services/audit_service.py** - 审计日志服务
- **services/feedback_service.py** - 用户反馈服务

### 新增页面文件
- **pages/audit_logs.py** - 管理员审计日志查看页面

### 新增测试文件
- **tests/test_interaction_audit.py** - 综合测试（13个测试用例，全部通过）

### 修改的现有文件
- **app.py** - 集成问答持久化、审计和反馈功能
- **pages/document_management.py** - 添加文档操作审计
- **models/database.py** - 支持 `:memory:` 数据库用于测试

## 2. 数据流

### 2.1 问答持久化流程
```
用户提问 → RAG查询 → 生成答案
    ↓
InteractionService.record_exchange()
    ↓
创建/复用 ChatSession → 保存 ChatMessage
    ↓
返回 {session_id, message_id} → 用于后续反馈
```

### 2.2 审计日志流程
```
RAG查询/文档操作 → 触发审计记录
    ↓
AuditService.record_rag_query() / record_document_operation()
    ↓
数据清洗（脱敏、截断、移除堆栈）
    ↓
保存 AuditLog（JSON格式元数据）
    ↓
管理员可通过 /audit_logs 页面查看
```

### 2.3 用户反馈流程
```
用户点击 👍/👎 → 前端调用 FeedbackService
    ↓
FeedbackService.upsert_feedback()
    ↓
权限检查（用户只能反馈自己的消息）
    ↓
Upsert模式（查询现有 → 更新或创建）
    ↓
记录 feedback_submit 审计事件
```

## 3. 审计 JSON 结构

### Schema Version 1
```json
{
  "schema_version": 1,
  "has_answer": true,
  "refusal_reason": null,
  "metrics": {
    "retrieved_count": 5,
    "filtered_count": 3,
    "valid_count": 3,
    "retrieval_time_ms": 120,
    "generation_time_ms": 850
  },
  "references": [
    {
      "document_id": 123,
      "document_version_id": 456,
      "source_name": "policy.pdf",
      "page": 5,
      "section": "第三章",
      "chunk_index": 2,
      "relevance_score": 0.89
    }
  ]
}
```

**隐私保护：**
- ❌ 不存储 `content` 字段（参考内容原文）
- ❌ 不存储完整的用户问题和系统答案（仅前200字摘要）
- ❌ 不存储内部堆栈跟踪（仅保留第一行错误描述）

## 4. 权限与隐私处理

### 4.1 权限控制
| 功能 | 员工 | 服务账号 | 管理员 |
|------|------|----------|--------|
| 提交反馈 | ✅ 仅自己的消息 | ✅ 仅自己的消息 | ✅ 仅自己的消息 |
| 查看审计日志 | ❌ | ❌ | ✅ |
| 管理文档 | ✅ 按空间权限 | ✅ 按空间权限 | ✅ 全部 |

### 4.2 隐私处理
- **敏感信息脱敏：** 使用正则表达式替换 API_KEY、password、secret、token 等
- **内容截断：** query_summary 限制200字符，feedback_content 限制500字符
- **堆栈清洗：** 错误信息只保留第一行，移除文件路径细节
- **引用元数据：** 审计JSON只记录文档ID和相关性分数，不记录原文

### 4.3 脱敏模式
```python
SENSITIVE_PATTERNS = [
    (r'api[_-]?key[=:\s]+\S+', '[API_KEY]'),
    (r'password[=:\s]+\S+', '[PASSWORD]'),
    (r'token[=:\s]+\S+', '[TOKEN]'),
    (r'secret[=:\s]+\S+', '[SECRET]'),
    (r'bearer\s+[\w\-\.]+', '[BEARER_TOKEN]'),
]
```

## 5. 测试覆盖

### 5.1 测试命令
```bash
# 运行所有审计和反馈测试
python -m pytest tests/test_interaction_audit.py -v

# 运行单个测试类
python -m pytest tests/test_interaction_audit.py::TestInteractionService -v
python -m pytest tests/test_interaction_audit.py::TestAuditService -v
python -m pytest tests/test_interaction_audit.py::TestFeedbackService -v
```

### 5.2 测试结果
```
tests/test_interaction_audit.py::TestInteractionService::test_record_exchange_creates_session PASSED [  7%]
tests/test_interaction_audit.py::TestInteractionService::test_record_exchange_reuses_session PASSED [ 15%]
tests/test_interaction_audit.py::TestInteractionService::test_record_refusal PASSED [ 23%]
tests/test_interaction_audit.py::TestAuditService::test_record_rag_query_success PASSED [ 30%]
tests/test_interaction_audit.py::TestAuditService::test_record_rag_query_refusal PASSED [ 38%]
tests/test_interaction_audit.py::TestAuditService::test_record_rag_query_error PASSED [ 46%]
tests/test_interaction_audit.py::TestAuditService::test_sanitize_sensitive_info PASSED [ 53%]
tests/test_interaction_audit.py::TestAuditService::test_list_logs_admin_only PASSED [ 61%]
tests/test_interaction_audit.py::TestAuditService::test_record_document_operation PASSED [ 69%]
tests/test_interaction_audit.py::TestFeedbackService::test_submit_feedback_success PASSED [ 76%]
tests/test_interaction_audit.py::TestFeedbackService::test_update_feedback PASSED [ 84%]
tests/test_interaction_audit.py::TestFeedbackService::test_feedback_permission PASSED [ 92%]
tests/test_interaction_audit.py::TestFeedbackService::test_feedback_content_length PASSED [100%]

======================= 13 passed, 14 warnings in 0.64s =======================
```

### 5.3 测试覆盖点
✅ **InteractionService (3个测试)**
- 创建新会话并记录问答
- 复用现有会话记录多轮对话
- 记录拒答（has_answer=False）

✅ **AuditService (6个测试)**
- 记录成功的RAG查询（is_success=True）
- 记录业务拒答（is_success=True, refusal_reason）
- 记录技术错误（is_success=False, generation_error）
- 敏感信息脱敏（API_KEY、password、secret、bearer token）
- 管理员权限检查（非管理员被拒绝）
- 文档操作审计（upload、reindex、deactivate等）

✅ **FeedbackService (4个测试)**
- 提交新反馈
- 更新现有反馈（upsert模式）
- 跨用户权限检查（用户不能反馈他人消息）
- 内容长度限制（500字符）

## 6. 数据库变更状态

**✅ 无数据库结构变更**
- 所有表（ChatSession、ChatMessage、Feedback、AuditLog）在之前已存在
- 本次实施仅新增服务层逻辑和业务流程
- 现有数据不受影响，无需迁移

## 7. 核心技术要点

### 7.1 业务拒答 vs 技术错误
```python
# 业务拒答 - is_success=True（系统正常工作）
refusal_reason in ['below_relevance_threshold', 'out_of_scope', 'space_permission_denied']

# 技术错误 - is_success=False（系统故障）
refusal_reason == 'generation_error'
```

### 7.2 Upsert 模式防止重复反馈
```python
existing = session.query(Feedback).filter_by(
    message_id=message_id, 
    user_id=user_id
).first()

if existing:
    existing.feedback_type = feedback_type  # 更新
    action = '更新'
else:
    feedback = Feedback(...)  # 创建
    action = '新增'
```

### 7.3 双层权限检查
```python
# 页面层检查
if st.session_state.get('role') != 'admin':
    st.error("仅管理员可访问")
    return

# 服务层检查
if user_role != 'admin':
    return {'success': False, 'error': 'permission_denied'}
```

## 8. 验收标准完成情况

| 标准 | 状态 | 证据 |
|------|------|------|
| 问答记录可在数据库查询 | ✅ | test_record_exchange_creates_session |
| 每条回答有稳定 message_id | ✅ | app.py 返回 message_id 并存储在 session_state |
| 反馈重复提交不重复记录 | ✅ | test_update_feedback (upsert模式) |
| 拒答记录为 is_success=True | ✅ | test_record_rag_query_refusal |
| 技术错误记录为 is_success=False | ✅ | test_record_rag_query_error |
| 审计JSON不含 content | ✅ | build_retrieved_docs_json() 排除 content 字段 |
| 敏感信息脱敏 | ✅ | test_sanitize_sensitive_info |
| 用户只能反馈自己消息 | ✅ | test_feedback_permission |
| 审计日志仅管理员访问 | ✅ | test_list_logs_admin_only |
| 文档操作审计 | ✅ | test_record_document_operation |

## 9. 未实施功能

**无** - 所有需求规格中的功能均已实施并通过测试。

## 10. 已知限制

1. **测试隔离问题：** 
   - 使用 `patch('models.database.get_engine')` 确保测试使用内存数据库
   - 每个测试后清理表数据，但 SQLite 外键约束警告无法消除

2. **脱敏正则表达式：**
   - 当前模式假设敏感信息使用 `=`、`:`、空格分隔符
   - 中文句子中的自然语言描述（如"我的密码是xxx"）可能无法识别

3. **审计日志分页：**
   - 当前使用 LIMIT/OFFSET 分页
   - 大数据集下可能性能不佳，未来可考虑游标分页

4. **反馈内容验证：**
   - 仅限制长度（500字符）
   - 未进行内容安全检查（XSS、SQL注入等由框架处理）

## 11. 部署注意事项

1. **首次部署：** 无需运行数据库迁移（表已存在）
2. **权限验证：** 确保管理员账户 role='admin' 正确设置
3. **性能监控：** 每次 RAG 查询增加 2 次数据库写入（ChatMessage + AuditLog）
4. **存储规划：** AuditLog 表会持续增长，建议定期归档或清理历史数据

## 12. 手动验收测试清单

### 问答持久化
- [ ] 在"智能问答"页面提问，确认数据库中生成 ChatSession 和 ChatMessage
- [ ] 在同一会话中提问多次，验证 session_id 保持不变
- [ ] 点击"新建会话"后提问，验证生成新的 session_id

### 用户反馈
- [ ] 对回答点击👍，确认反馈成功
- [ ] 对同一回答再次点击👎，验证反馈被更新而不是新增记录
- [ ] 查询数据库 Feedback 表，确认 message_id 和 user_id 关联正确

### 审计日志
- [ ] 以管理员身份访问 `/audit_logs` 页面，查看审计记录
- [ ] 以普通用户身份访问，确认被拒绝
- [ ] 执行 RAG 查询、文档上传、文档重索引等操作，验证审计日志生成
- [ ] 检查审计日志中的 query_summary 是否已脱敏
- [ ] 验证拒答场景的 is_success=True，技术错误的 is_success=False

### 隐私保护
- [ ] 检查审计日志的 retrieved_docs JSON，确认不包含 content 字段
- [ ] 提交包含 "API_KEY=xxx" 的查询，验证审计日志中已替换为 [API_KEY]
- [ ] 触发技术错误，检查 error_message 不包含完整堆栈跟踪

---

**实施完成日期：** 2026-09-08  
**测试通过率：** 13/13 (100%)  
**代码审查状态：** ✅ 已完成  
**文档状态：** ✅ 已完成
