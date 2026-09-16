"""
知识库管理模块

使用 FAISS 向量数据库（替代 Chroma，解决 Windows DLL 兼容性问题）：
- add_document: 切分 + embedding + 存储
- query: 语义检索（支持 section_type 过滤）
- has_document / get_all_titles / get_document_count: 状态查询
- clear: 清空（用于重置/测试）

设计决策：
- FAISS IndexFlatL2：本地纯 Python/C++ 实现，Windows 兼容性好
- 持久化：save_local / load_local 到磁盘
- Embedding: ZhipuAI embedding-3（2048 dims）
- 去重：基于规范化前的论文标题精确匹配
- 元数据：每个 chunk 携带 paper_title 和 section_type
"""

import json
import logging
import os
from typing import Optional

import config
from rag.chunking import chunk_paper_content
from utils.llm import make_embeddings

logger = logging.getLogger(__name__)

class KnowledgeBase:
    def __init__(
        self,
        data_dir: str | None = None,
        embeddings=None,
        trust_local_index: bool = True,
    ):
        """创建知识库。

        Args:
            data_dir: 索引根目录。默认使用 config.KB_DATA_DIR；评估器会传入临时目录，
                以避免清空或污染用户的真实知识库。
            embeddings: 可选的 Embeddings 实例，便于测试时注入 fake。
            trust_local_index: 是否信任并加载本地 FAISS pickle。仅应对本程序自己
                生成且路径受控的索引设为 True。
        """
        self.data_dir = os.path.abspath(data_dir or config.KB_DATA_DIR)
        self._faiss_index_dir = os.path.join(self.data_dir, "faiss_index")
        self._meta_file = os.path.join(self._faiss_index_dir, "titles.json")
        self._trust_local_index = trust_local_index
        self.embeddings = embeddings if embeddings is not None else make_embeddings()
        self._paper_titles: set[str] = set()
        self._vectorstore = None  # lazy-init on first add
        self._pending_save = False

        # 尝试从磁盘恢复
        self._load_from_disk()
        logger.info(f"KnowledgeBase initialized. Existing papers: {len(self._paper_titles)}")

    # ─────────────────────────────────────────────
    # 公开接口
    # ─────────────────────────────────────────────

    def has_document(self, paper_title: str) -> bool:
        return paper_title in self._paper_titles

    def add_document(self, title: str, content: str):
        """将论文信息存入知识库。"""
        from langchain_community.vectorstores import FAISS

        chunks = chunk_paper_content(content, paper_title=title)
        if not chunks:
            logger.warning(f"No valid chunks from paper: {title}")
            return

        texts = [c["text"] for c in chunks]
        metadatas = [c["metadata"] for c in chunks]

        if self._vectorstore is None:
            self._vectorstore = FAISS.from_texts(
                texts=texts,
                embedding=self.embeddings,
                metadatas=metadatas,
            )
        else:
            self._vectorstore.add_texts(texts=texts, metadatas=metadatas)

        self._paper_titles.add(title)
        self._pending_save = True          # 标记有未持久化的更改
        logger.info(f"Saved {len(chunks)} chunks for: {title}")

    def flush(self):
        """将内存中的索引持久化到磁盘（批量写入，避免每篇论文都触发一次全量序列化）。"""
        if getattr(self, "_pending_save", False):
            self._save_to_disk()
            self._pending_save = False

    def query(
        self,
        query_text: str,
        top_k: int = None,
        section_filter: Optional[str] = None,
    ) -> list[tuple[str, dict]]:
        """
        语义检索。

        Args:
            query_text: 检索问题
            top_k: 返回条目数
            section_filter: 可选，按 section_type 过滤（事后过滤）

        Returns:
            list of (content_text, metadata) tuples
        """
        results, _ = self.query_with_diagnostics(
            query_text=query_text,
            top_k=top_k,
            section_filter=section_filter,
        )
        return results

    def query_with_diagnostics(
        self,
        query_text: str,
        top_k: int = None,
        section_filter: Optional[str] = None,
    ) -> tuple[list[tuple[str, dict]], dict]:
        """语义检索并返回过滤诊断，避免 section fallback 静默发生。"""
        diagnostics = {
            "section_filter": section_filter or "",
            "filter_hit": None if not section_filter else False,
            "fallback_used": False,
        }

        if self._vectorstore is None:
            return [], diagnostics

        top_k = max(1, top_k or config.RETRIEVAL_TOP_K)

        if section_filter:
            # FAISS 不支持原生过滤，多取再手动筛选。
            # over-fetch 倍数取 max(20, top_k*8) 保证在大 KB 下也能命中足够多同类 chunk
            fetch_k = max(20, top_k * 8)
            docs = self._vectorstore.similarity_search(query_text, k=fetch_k)
            filtered = [
                d for d in docs
                if d.metadata.get("section_type") == section_filter
            ]
            if filtered:
                diagnostics["filter_hit"] = True
                docs = filtered[:top_k]
            else:
                diagnostics["fallback_used"] = True
                docs = docs[:top_k]
        else:
            docs = self._vectorstore.similarity_search(query_text, k=top_k)

        return [(d.page_content, d.metadata) for d in docs], diagnostics

    def get_all_titles(self) -> list[str]:
        return list(self._paper_titles)

    def get_document_count(self) -> int:
        return len(self._paper_titles)

    def clear(self):
        """清空知识库。"""
        # 删除磁盘缓存前先验证绝对路径，避免异常配置扩大删除范围。
        index_dir = os.path.abspath(self._faiss_index_dir)
        data_dir = os.path.abspath(self.data_dir)
        try:
            inside_data_dir = os.path.commonpath([index_dir, data_dir]) == data_dir
        except ValueError:
            inside_data_dir = False
        if not inside_data_dir or index_dir == data_dir:
            raise ValueError(f"Refusing to clear unsafe index path: {index_dir}")

        self._vectorstore = None
        self._paper_titles.clear()
        self._pending_save = False

        import shutil
        if os.path.exists(index_dir):
            shutil.rmtree(index_dir)
        logger.info("Knowledge base cleared.")

    # ─────────────────────────────────────────────
    # 持久化辅助
    # ─────────────────────────────────────────────

    def _save_to_disk(self):
        try:
            from langchain_community.vectorstores import FAISS
            os.makedirs(self._faiss_index_dir, exist_ok=True)
            if self._vectorstore:
                self._vectorstore.save_local(self._faiss_index_dir)
            with open(self._meta_file, "w", encoding="utf-8") as f:
                json.dump(list(self._paper_titles), f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to persist KB to disk: {e}")

    def _load_from_disk(self):
        try:
            from langchain_community.vectorstores import FAISS
            if (
                self._trust_local_index
                and os.path.exists(os.path.join(self._faiss_index_dir, "index.faiss"))
            ):
                self._vectorstore = FAISS.load_local(
                    self._faiss_index_dir,
                    self.embeddings,
                    allow_dangerous_deserialization=True,
                )
                logger.info("FAISS index loaded from disk.")
            elif not self._trust_local_index:
                logger.info("Local FAISS index loading disabled (trust_local_index=False).")
            if self._trust_local_index and os.path.exists(self._meta_file):
                with open(self._meta_file, "r", encoding="utf-8") as f:
                    self._paper_titles = set(json.load(f))
        except Exception as e:
            logger.warning(f"Failed to load KB from disk: {e}")
