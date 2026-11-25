from abc import ABC, abstractmethod
import os
import re

class Retriever(ABC):
    @abstractmethod
    def search(self, query: str, limit: int = 3) -> list: ...

class SQLiteRetriever(Retriever):
    def __init__(self, db): self.db=db
    def search(self, query, limit=3):
        words = re.findall(r'[가-힣A-Za-z0-9]+', query)[:8]
        if not words: return []
        rows = self.db.execute('SELECT title,content,source,version,confidence FROM rag_chunks JOIN rag_documents ON rag_documents.id=document_id ORDER BY rag_chunks.id').fetchall()
        ranked = [(sum(word in (r['title']+' '+r['content']) for word in words), dict(r)) for r in rows]
        return [r for score,r in sorted(ranked,key=lambda x:-x[0]) if score][:max(0,min(limit,10))]

class TavilySearchProvider(ABC):
    allowed_domains = ('foodsafetykorea.go.kr', 'mfds.go.kr')
    timeout_seconds = 5
    @property
    def enabled(self): return False  # Interface only; no network implementation.

    @property
    def configured(self): return bool(os.getenv('TAVILY_API_KEY'))
    @abstractmethod
    def search_read_only(self, query: str) -> list:
        """Only for current external information; never send personal data."""
        raise NotImplementedError
