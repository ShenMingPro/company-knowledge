# RAG 系统问题分析与修复方案

## 问题清单

### 问题1：无权限内容返回 has_answer=True（严重）
**现象**：employee 无售后权限，却问"售后SLA的P0响应时限"，返回 has_answer=True，并编造了"15分钟内"

**根因**：
- 检索到无关文档（产品/制度文档）
- 阈值过宽（RELEVANCE_THRESHOLD=1.2），无关文档通过过滤
- LLM 基于无关内容编造答案

### 问题2：库外内容拒答失效（严重）
**现象**：问"怎么制作原子弹"，返回 has_answer=True

**根因**：
1. `RELEVANCE_THRESHOLD = 1.2` 过于宽松（注释写明"放宽以提高召回率"）
2. `filter_by_relevance` 对 BM25-only 候选直接放行（distance is None → passed=True）
3. BM25 关键词命中即可通过，无向量相关性校验

### 问题3：引用去重失效（中等）
**现象**：问"差旅报销时限"，3条引用全是同一份《09-差旅制度》

**根因**：
- 去重键是 `version_id_chunk_index`（rag_service.py:294）
- 只去重"同一分片"，不去重"同一文档的不同分片"

### 问题4：Rerank与阈值顺序（中等，但当前设计合理）
**现象**：流程是"先阈值过滤 → 再 Rerank"，Rerank 无法救回被过滤掉的文档

**分析**：
- 当前设计是对的：Rerank 不应该处理无关文档
- 问题不在顺序，而在阈值过宽导致垃圾文档进入

### 问题5：Reranker 500 错误（低）
**现象**：DashScope Rerank 失败 status_code=500

**需要验证**：API Key、配额、文本长度、模型名

### 问题6：DB 膨胀（提示，无功能影响）
**现象**：documents=125，有效约44，document_versions=130

**分析**：正常的版本管理，不影响功能

---

## 修复方案

### 修复1：收紧相关性阈值
```python
# rag/rag_service.py
# 旧值：RELEVANCE_THRESHOLD = 1.2  # 放宽以提高召回率
# 新值：
RELEVANCE_THRESHOLD = 0.9  # 收紧阈值，防止无关文档通过
```

**理由**：
- L2距离范围 0-2，归一化 embedding 典型值：
  - 0-0.5：高度相关
  - 0.5-0.9：中等相关
  - 0.9-1.3：弱相关
  - 1.3+：不相关
- 1.2 是弱相关，容易让无关文档通过

### 修复2：BM25-only 候选增加二次验证
```python
def filter_by_relevance(self, docs, scores):
    """
    按相关性阈值过滤文档
    - 向量候选：使用距离阈值
    - BM25-only 候选：必须有明确的 BM25 分数且 > 0
    """
    for doc, distance in zip(docs, scores):
        # BM25-only 候选
        if distance is None:
            # 检查是否有有效的 BM25 分数
            bm25_score = doc.metadata.get('bm25_score', 0)
            if bm25_score > 0:
                filtered_docs.append(doc)
                # 标记为通过，但不是高置信度
                detail = {
                    "raw_distance": None,
                    "relevance_score": None,
                    "bm25_score": bm25_score,
                    "passed": True,
                    "source": "bm25_only"
                }
            else:
                # BM25 分数无效，拒绝
                detail = {
                    "raw_distance": None,
                    "relevance_score": None,
                    "bm25_score": bm25_score,
                    "passed": False,
                    "source": "bm25_invalid"
                }
```

**问题**：当前 BM25-only 候选没有携带 bm25_score 到 metadata

### 修复3：引用去重增加同文档聚合
```python
def format_references(self, docs, scores, extra_fields):
    """
    引用去重策略：
    1. 同一分片去重（version_id_chunk_index）
    2. 同一文档只保留相关度最高的一条
    """
    references = []
    seen_chunks = set()  # 去重：同一分片
    seen_documents = {}  # 聚合：同一文档只取最佳
    
    for idx, (doc, distance) in enumerate(zip(docs, scores), 1):
        version_id = doc.metadata.get('document_version_id')
        chunk_index = doc.metadata.get('chunk_index', 0)
        document_id = doc.metadata.get('document_id')
        
        # 去重：同一分片
        chunk_key = f"{version_id}_{chunk_index}"
        if chunk_key in seen_chunks:
            continue
        seen_chunks.add(chunk_key)
        
        # 聚合：同一文档只取相关度最高的
        if document_id in seen_documents:
            # 比较相关度（距离越小越好）
            existing_distance = seen_documents[document_id]['distance']
            if distance is not None and existing_distance is not None:
                if distance < existing_distance:
                    # 当前更好，替换
                    seen_documents[document_id] = {
                        'doc': doc,
                        'distance': distance,
                        'index': idx
                    }
            continue  # 跳过，已有该文档
        
        # 首次遇到该文档
        seen_documents[document_id] = {
            'doc': doc,
            'distance': distance,
            'index': idx
        }
    
    # 构建最终引用列表
    for doc_id, info in seen_documents.items():
        # 构建引用信息...
```

---

## 修复优先级

### P0（必须立即修复）
1. 收紧 RELEVANCE_THRESHOLD：1.2 → 0.9
2. BM25-only 候选验证逻辑

### P1（重要）
3. 引用去重策略优化

### P2（可选）
4. Reranker 500 错误调试
5. DB 清理脚本（非功能性）

---

## 修复策略

**顾全大局原则**：
1. 不破坏现有测试（64个测试全部通过）
2. 不影响混合检索、Query改写、Reranker功能
3. 修复后必须回归所有测试
4. 优先修复拒答失效问题（防幻觉）

**测试验证**：
1. 新增测试：库外内容拒答
2. 新增测试：无权限内容拒答
3. 新增测试：同文档引用去重
4. 回归测试：现有64个测试
