"""
内容结构分块策略路由器
根据文档内容结构选择合适的分块策略
"""
import re
from typing import List, Dict, Any, Optional
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.logger_handler import logger


class ContentStructureDetector:
    """内容结构检测器"""

    @staticmethod
    def detect_structure(text: str) -> str:
        """
        检测文本内容结构
        :param text: 文本内容
        :return: 结构类型 (qa, section, steps, paragraph, recursive)
        """
        if not text or not text.strip():
            return 'recursive'

        # 优先级1: 问答结构
        if ContentStructureDetector._is_qa_structure(text):
            return 'qa'

        # 优先级2: 章节结构
        if ContentStructureDetector._is_section_structure(text):
            return 'section'

        # 优先级3: 步骤列表结构
        if ContentStructureDetector._is_steps_structure(text):
            return 'steps'

        # 优先级4: 普通段落结构
        if ContentStructureDetector._is_paragraph_structure(text):
            return 'paragraph'

        # 兜底: 递归字符分块
        return 'recursive'

    @staticmethod
    def _is_qa_structure(text: str) -> bool:
        """检测是否为问答结构"""
        # FAQ标记
        faq_markers = ['FAQ', 'faq', '常见问题', '问答', 'Q&A', 'Q&amp;A']
        if any(marker in text for marker in faq_markers):
            return True

        # 问答对模式
        qa_patterns = [
            r'问题[:：]\s*.+答案[:：]',
            r'问[:：]\s*.+答[:：]',
            r'Q[:：]\s*.+A[:：]',
            r'Question[:：]\s*.+Answer[:：]',
        ]

        for pattern in qa_patterns:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                return True

        # 多个问答对
        q_count = len(re.findall(r'^[问Q][:：\s]', text, re.MULTILINE | re.IGNORECASE))
        a_count = len(re.findall(r'^[答A][:：\s]', text, re.MULTILINE | re.IGNORECASE))

        return q_count >= 2 and a_count >= 2

    @staticmethod
    def _is_section_structure(text: str) -> bool:
        """检测是否为章节结构"""
        # Markdown标题
        md_headers = len(re.findall(r'^#{1,6}\s+.+', text, re.MULTILINE))
        if md_headers >= 1:  # 降低阈值，有标题就算章节结构
            return True

        # 数字章节编号 (1. 1.1 等)
        num_sections = len(re.findall(r'^\d+(\.\d+)+\s+.{2,50}$', text, re.MULTILINE))
        if num_sections >= 1:
            return True

        # 中文章节
        cn_sections = len(re.findall(r'^第[一二三四五六七八九十百]+章.{2,50}$', text, re.MULTILINE))
        cn_sections += len(re.findall(r'^[一二三四五六七八九十]+、.{2,50}$', text, re.MULTILINE))
        if cn_sections >= 1:
            return True

        return False

    @staticmethod
    def _is_steps_structure(text: str) -> bool:
        """检测是否为步骤列表结构"""
        # 数字步骤 1. 2. 3.
        num_steps = len(re.findall(r'^\d+[.、]\s+', text, re.MULTILINE))
        if num_steps >= 3:
            return True

        # 括号步骤 (1) (2) (3)
        paren_steps = len(re.findall(r'^[（(]\d+[)）]\s+', text, re.MULTILINE))
        if paren_steps >= 3:
            return True

        # 列表标记
        list_markers = len(re.findall(r'^[-•]\s+', text, re.MULTILINE))
        if list_markers >= 3:
            return True

        return False

    @staticmethod
    def _is_paragraph_structure(text: str) -> bool:
        """检测是否为普通段落结构"""
        # 存在多个自然段落（双换行分隔）
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        return len(paragraphs) >= 2


class ChunkStrategy:
    """分块策略基类"""

    def __init__(self, chunk_size: int, chunk_overlap: int, separators: List[str]):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators
        self.fallback_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators,
            length_function=len,
        )

    def split(self, doc: Document) -> List[Document]:
        """分块方法，子类实现"""
        raise NotImplementedError


class QAChunkStrategy(ChunkStrategy):
    """问答结构分块策略"""

    def split(self, doc: Document) -> List[Document]:
        """将问答对尽量保持在同一分块"""
        text = doc.page_content
        chunks = []

        # 提取问答对
        qa_pairs = self._extract_qa_pairs(text)

        if not qa_pairs:
            # 无法提取问答对，使用递归分块
            return self.fallback_splitter.split_documents([doc])

        for qa in qa_pairs:
            qa_text = qa['question'] + '\n' + qa['answer']

            if len(qa_text) <= self.chunk_size:
                # 问答对在大小限制内，保持完整
                chunk_doc = Document(
                    page_content=qa_text,
                    metadata={
                        **doc.metadata,
                        'chunk_strategy': 'question_answer',
                        'question': qa['question'][:100]  # 保留问题摘要
                    }
                )
                chunks.append(chunk_doc)
            else:
                # 问答对过长，递归切分
                temp_doc = Document(page_content=qa_text, metadata=doc.metadata)
                sub_chunks = self.fallback_splitter.split_documents([temp_doc])
                for sub in sub_chunks:
                    sub.metadata['chunk_strategy'] = 'question_answer_split'
                    sub.metadata['question'] = qa['question'][:100]
                chunks.extend(sub_chunks)

        return chunks if chunks else self.fallback_splitter.split_documents([doc])

    def _extract_qa_pairs(self, text: str) -> List[Dict[str, str]]:
        """提取问答对"""
        pairs = []

        # 模式1: 问题：...答案：...
        pattern1 = r'问题[:：]\s*(.+?)答案[:：]\s*(.+?)(?=问题[:：]|$)'
        matches = re.findall(pattern1, text, re.DOTALL | re.IGNORECASE)
        for q, a in matches:
            pairs.append({'question': q.strip(), 'answer': a.strip()})

        # 模式2: Q: ... A: ...
        if not pairs:
            pattern2 = r'[Qq][:：]\s*(.+?)[Aa][:：]\s*(.+?)(?=[Qq][:：]|$)'
            matches = re.findall(pattern2, text, re.DOTALL)
            for q, a in matches:
                pairs.append({'question': q.strip(), 'answer': a.strip()})

        # 模式3: 问：... 答：...
        if not pairs:
            pattern3 = r'问[:：]\s*(.+?)答[:：]\s*(.+?)(?=问[:：]|$)'
            matches = re.findall(pattern3, text, re.DOTALL)
            for q, a in matches:
                pairs.append({'question': q.strip(), 'answer': a.strip()})

        return pairs


class SectionChunkStrategy(ChunkStrategy):
    """章节结构分块策略"""

    def split(self, doc: Document) -> List[Document]:
        """按章节分块"""
        text = doc.page_content
        sections = self._extract_sections(text)

        if not sections:
            return self.fallback_splitter.split_documents([doc])

        chunks = []
        for section in sections:
            section_text = section['title'] + '\n' + section['content']

            if len(section_text) <= self.chunk_size:
                # 章节在大小限制内
                chunk_doc = Document(
                    page_content=section_text,
                    metadata={
                        **doc.metadata,
                        'chunk_strategy': 'section',
                        'section_title': section['title']
                    }
                )
                chunks.append(chunk_doc)
            else:
                # 章节过长，递归切分但保留标题
                temp_doc = Document(page_content=section_text, metadata=doc.metadata)
                sub_chunks = self.fallback_splitter.split_documents([temp_doc])
                for sub in sub_chunks:
                    sub.metadata['chunk_strategy'] = 'section_split'
                    sub.metadata['section_title'] = section['title']
                chunks.extend(sub_chunks)

        return chunks if chunks else self.fallback_splitter.split_documents([doc])

    def _extract_sections(self, text: str) -> List[Dict[str, str]]:
        """提取章节"""
        sections = []
        lines = text.split('\n')

        current_title = None
        current_content = []

        for line in lines:
            # 检测是否为标题
            if self._is_section_title(line):
                # 保存上一个章节
                if current_title is not None:
                    sections.append({
                        'title': current_title,
                        'content': '\n'.join(current_content).strip()
                    })

                current_title = line.strip()
                current_content = []
            else:
                if current_title is not None:
                    current_content.append(line)

        # 保存最后一个章节
        if current_title is not None:
            sections.append({
                'title': current_title,
                'content': '\n'.join(current_content).strip()
            })

        return sections

    def _is_section_title(self, line: str) -> bool:
        """判断是否为章节标题"""
        line = line.strip()
        if not line or len(line) > 100:
            return False

        # Markdown标题
        if re.match(r'^#{1,6}\s+.+', line):
            return True

        # 数字章节
        if re.match(r'^\d+(\.\d+)*\.?\s+.{2,50}$', line):
            return True

        # 中文章节
        if re.match(r'^第[一二三四五六七八九十百]+章.+', line):
            return True

        if re.match(r'^[一二三四五六七八九十]+、.+', line):
            return True

        return False


class StepsChunkStrategy(ChunkStrategy):
    """步骤列表分块策略"""

    def split(self, doc: Document) -> List[Document]:
        """按步骤分块"""
        text = doc.page_content
        steps = self._extract_steps(text)

        if not steps:
            return self.fallback_splitter.split_documents([doc])

        chunks = []
        current_chunk = []
        current_length = 0

        for step in steps:
            step_length = len(step)

            if step_length > self.chunk_size:
                # 单个步骤过长，先保存当前累积
                if current_chunk:
                    chunk_text = '\n'.join(current_chunk)
                    chunk_doc = Document(
                        page_content=chunk_text,
                        metadata={**doc.metadata, 'chunk_strategy': 'steps'}
                    )
                    chunks.append(chunk_doc)
                    current_chunk = []
                    current_length = 0

                # 递归切分该步骤
                temp_doc = Document(page_content=step, metadata=doc.metadata)
                sub_chunks = self.fallback_splitter.split_documents([temp_doc])
                for sub in sub_chunks:
                    sub.metadata['chunk_strategy'] = 'steps_split'
                chunks.extend(sub_chunks)
            elif current_length + step_length > self.chunk_size:
                # 累积超过限制，保存当前chunk
                chunk_text = '\n'.join(current_chunk)
                chunk_doc = Document(
                    page_content=chunk_text,
                    metadata={**doc.metadata, 'chunk_strategy': 'steps'}
                )
                chunks.append(chunk_doc)
                current_chunk = [step]
                current_length = step_length
            else:
                # 累积该步骤
                current_chunk.append(step)
                current_length += step_length

        # 保存最后的chunk
        if current_chunk:
            chunk_text = '\n'.join(current_chunk)
            chunk_doc = Document(
                page_content=chunk_text,
                metadata={**doc.metadata, 'chunk_strategy': 'steps'}
            )
            chunks.append(chunk_doc)

        return chunks if chunks else self.fallback_splitter.split_documents([doc])

    def _extract_steps(self, text: str) -> List[str]:
        """提取步骤"""
        steps = []
        lines = text.split('\n')

        current_step = []
        in_steps = False

        for line in lines:
            if self._is_step_marker(line):
                # 保存上一个步骤
                if current_step:
                    steps.append('\n'.join(current_step).strip())
                current_step = [line]
                in_steps = True
            else:
                if in_steps and line.strip():
                    # 如果已经在步骤中，且不是空行，继续添加
                    current_step.append(line)
                elif not line.strip() and current_step:
                    # 空行可能是步骤之间的分隔，继续保持
                    pass

        # 保存最后一个步骤
        if current_step:
            steps.append('\n'.join(current_step).strip())

        return [s for s in steps if s]  # 过滤空步骤

    def _is_step_marker(self, line: str) -> bool:
        """判断是否为步骤标记"""
        line = line.strip()
        if not line:
            return False

        # 数字步骤
        if re.match(r'^\d+[.、]\s+', line):
            return True

        # 括号步骤
        if re.match(r'^[（(]\d+[)）]\s+', line):
            return True

        # 列表标记
        if re.match(r'^[-•]\s+', line):
            return True

        return False


class ParagraphChunkStrategy(ChunkStrategy):
    """段落分块策略"""

    def split(self, doc: Document) -> List[Document]:
        """按段落分块"""
        text = doc.page_content
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

        if not paragraphs:
            return self.fallback_splitter.split_documents([doc])

        chunks = []
        current_chunk = []
        current_length = 0

        for para in paragraphs:
            para_length = len(para)

            if para_length > self.chunk_size:
                # 段落过长，先保存当前累积
                if current_chunk:
                    chunk_text = '\n\n'.join(current_chunk)
                    chunk_doc = Document(
                        page_content=chunk_text,
                        metadata={**doc.metadata, 'chunk_strategy': 'paragraph'}
                    )
                    chunks.append(chunk_doc)
                    current_chunk = []
                    current_length = 0

                # 递归切分该段落
                temp_doc = Document(page_content=para, metadata=doc.metadata)
                sub_chunks = self.fallback_splitter.split_documents([temp_doc])
                for sub in sub_chunks:
                    sub.metadata['chunk_strategy'] = 'paragraph_split'
                chunks.extend(sub_chunks)
            elif current_length + para_length > self.chunk_size:
                # 累积超过限制
                chunk_text = '\n\n'.join(current_chunk)
                chunk_doc = Document(
                    page_content=chunk_text,
                    metadata={**doc.metadata, 'chunk_strategy': 'paragraph'}
                )
                chunks.append(chunk_doc)
                current_chunk = [para]
                current_length = para_length
            else:
                # 累积段落
                current_chunk.append(para)
                current_length += para_length + 2  # +2 for '\n\n'

        # 保存最后的chunk
        if current_chunk:
            chunk_text = '\n\n'.join(current_chunk)
            chunk_doc = Document(
                page_content=chunk_text,
                metadata={**doc.metadata, 'chunk_strategy': 'paragraph'}
            )
            chunks.append(chunk_doc)

        return chunks if chunks else self.fallback_splitter.split_documents([doc])


class RecursiveChunkStrategy(ChunkStrategy):
    """递归字符分块策略（兜底）"""

    def split(self, doc: Document) -> List[Document]:
        """递归字符分块"""
        chunks = self.fallback_splitter.split_documents([doc])
        for chunk in chunks:
            chunk.metadata['chunk_strategy'] = 'recursive'
        return chunks


class ChunkStrategyRouter:
    """分块策略路由器"""

    def __init__(self, chunk_size: int = 200, chunk_overlap: int = 20,
                 separators: List[str] = None):
        if separators is None:
            separators = ["\n\n", "\n", ".", "!", "?", "。", "！", "？", " ", ""]

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators

        # 初始化各策略
        self.strategies = {
            'qa': QAChunkStrategy(chunk_size, chunk_overlap, separators),
            'section': SectionChunkStrategy(chunk_size, chunk_overlap, separators),
            'steps': StepsChunkStrategy(chunk_size, chunk_overlap, separators),
            'paragraph': ParagraphChunkStrategy(chunk_size, chunk_overlap, separators),
            'recursive': RecursiveChunkStrategy(chunk_size, chunk_overlap, separators),
        }

        self.detector = ContentStructureDetector()

    def split(self, documents: List[Document], source_type: str = None) -> List[Document]:
        """
        统一分块入口
        :param documents: 解析后的Document列表
        :param source_type: 源文件类型 (pdf, txt, docx, xlsx)
        :return: 分块后的Document列表
        """
        all_chunks = []

        for doc in documents:
            try:
                chunks = self._split_single_document(doc, source_type)
                all_chunks.extend(chunks)
            except Exception as e:
                logger.error(f"分块失败，使用兜底策略: {str(e)}", exc_info=True)
                # 兜底：使用递归分块
                fallback_chunks = self.strategies['recursive'].split(doc)
                all_chunks.extend(fallback_chunks)

        return all_chunks

    def _split_single_document(self, doc: Document, source_type: str) -> List[Document]:
        """分块单个Document"""
        # 处理 DOCX 结构化内容
        if source_type == 'docx' and 'block_type' in doc.metadata:
            return self._split_docx_block(doc)

        # 检测内容结构
        structure_type = self.detector.detect_structure(doc.page_content)

        # 选择策略
        strategy = self.strategies.get(structure_type, self.strategies['recursive'])

        # 执行分块
        chunks = strategy.split(doc)

        return chunks

    def _split_docx_block(self, doc: Document) -> List[Document]:
        """处理 DOCX 结构化块"""
        block_type = doc.metadata.get('block_type')

        if block_type == 'heading':
            # 标题作为独立块，标记策略
            doc.metadata['chunk_strategy'] = 'heading'
            return [doc]

        elif block_type == 'table':
            # 表格处理
            return self._split_table(doc)

        elif block_type == 'paragraph':
            # 段落内容检测结构
            structure_type = self.detector.detect_structure(doc.page_content)
            strategy = self.strategies.get(structure_type, self.strategies['recursive'])
            chunks = strategy.split(doc)

            # 保留 block_type
            for chunk in chunks:
                chunk.metadata['block_type'] = 'paragraph'

            return chunks

        else:
            # 未知类型，使用递归分块
            return self.strategies['recursive'].split(doc)

    def _split_table(self, doc: Document) -> List[Document]:
        """分割表格"""
        text = doc.page_content
        row_count = doc.metadata.get('row_count', 0)

        if len(text) <= self.chunk_size:
            # 表格较短，保持完整
            doc.metadata['chunk_strategy'] = 'table'
            return [doc]

        # 表格过长，按行分割
        lines = text.split('\n')
        if not lines:
            return [doc]

        chunks = []
        current_chunk = []
        current_length = 0

        # 保留表头（第一行）
        header = lines[0] if lines else ''

        for i, line in enumerate(lines):
            line_length = len(line) + 1  # +1 for '\n'

            if i == 0:
                # 表头
                current_chunk.append(line)
                current_length += line_length
            elif current_length + line_length > self.chunk_size:
                # 超过限制，保存当前chunk
                if current_chunk:
                    chunk_text = '\n'.join(current_chunk)
                    chunk_doc = Document(
                        page_content=chunk_text,
                        metadata={
                            **doc.metadata,
                            'chunk_strategy': 'table_split'
                        }
                    )
                    chunks.append(chunk_doc)

                # 开始新chunk（包含表头）
                current_chunk = [header, line]
                current_length = len(header) + 1 + line_length
            else:
                current_chunk.append(line)
                current_length += line_length

        # 保存最后的chunk
        if current_chunk and len(current_chunk) > 1:  # 确保不只是表头
            chunk_text = '\n'.join(current_chunk)
            chunk_doc = Document(
                page_content=chunk_text,
                metadata={
                    **doc.metadata,
                    'chunk_strategy': 'table_split'
                }
            )
            chunks.append(chunk_doc)

        return chunks if chunks else [doc]
