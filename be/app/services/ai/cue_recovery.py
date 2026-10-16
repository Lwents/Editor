"""Durable validated cue results, including completed sub-batches."""
import hashlib
import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from app.services.persistence import PersistenceError


class CueRecovery:
    def __init__(self, path: Path, namespace: str):
        self.path, self.namespace = path, namespace
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS cues (namespace TEXT, input TEXT, text TEXT NOT NULL, memory TEXT NOT NULL, PRIMARY KEY(namespace,input))')

    @contextmanager
    def connect(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=10)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            with connection:
                yield connection
        except sqlite3.Error as exc:
            raise PersistenceError('CUE_STORAGE_FAILED: Không mở được dữ liệu câu đã dịch; giữ nguyên để khôi phục.') from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def key(item):
        return hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def load(self, batch):
        texts, memories = {}, []
        try:
            with self.connect() as connection:
                for item in batch:
                    row = connection.execute('SELECT text,memory FROM cues WHERE namespace=? AND input=?', (self.namespace,self.key(item))).fetchone()
                    if row:
                        text, memory = row[0], json.loads(row[1])
                        if not isinstance(text,str) or not text.strip() or not isinstance(memory,dict):
                            raise PersistenceError('CUE_DATA_INVALID: Câu lưu không hợp lệ; không tự bỏ qua dữ liệu.')
                        texts[int(item['id'])] = text
                        memories.append(memory)
            return texts, memories
        except (sqlite3.Error, ValueError) as exc:
            raise PersistenceError('CUE_STORAGE_FAILED: Không đọc được câu đã lưu; cần kiểm tra ổ đĩa.') from exc

    def save(self, batch, texts, updates):
        if not texts:
            return
        try:
            with self.connect() as connection:
                connection.executemany('INSERT OR REPLACE INTO cues VALUES (?,?,?,?)',
                    [(self.namespace,self.key(item),texts[int(item['id'])],json.dumps(updates,ensure_ascii=False))
                     for item in batch if int(item['id']) in texts])
        except sqlite3.Error as exc:
            raise PersistenceError('CUE_SAVE_FAILED: Không lưu được câu vừa dịch. Đã dừng gửi thêm yêu cầu; kiểm tra dung lượng/quyền ghi.') from exc
