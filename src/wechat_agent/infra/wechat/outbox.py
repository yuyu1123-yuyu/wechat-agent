"""Local durable reply queue. Never include this database in a source release."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class Outbox:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS replies (
                id INTEGER PRIMARY KEY, target TEXT, text TEXT, chat_id TEXT,
                created REAL, due REAL, attempts INTEGER DEFAULT 0,
                state TEXT DEFAULT 'pending')''')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def add(self, target, text, chat_id):
        with self._connect() as db:
            cur = db.execute('INSERT INTO replies(target,text,chat_id,created,due) VALUES(?,?,?,?,?)',
                             (target, text, chat_id, time.time(), time.time()))
            return cur.lastrowid

    def next(self):
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            # Maintain FIFO within each chat, allow other chats to proceed.
            row = db.execute('''SELECT * FROM replies r WHERE state='pending' AND due<=?
                AND NOT EXISTS(SELECT 1 FROM replies older WHERE older.state='pending'
                AND older.target=r.target AND older.id<r.id) ORDER BY id LIMIT 1''',
                (time.time(),)).fetchone()
            return dict(row) if row else None

    def finish(self, ident):
        with self._connect() as db:
            db.execute('DELETE FROM replies WHERE id=?', (ident,))

    def retry(self, ident):
        with self._connect() as db:
            row = db.execute('SELECT attempts FROM replies WHERE id=?', (ident,)).fetchone()
            if row:
                delay = min(60, 5 * 2 ** min(row[0], 4))
                db.execute('UPDATE replies SET attempts=attempts+1,due=? WHERE id=?',
                           (time.time() + delay, ident))

    def hold(self, ident):
        # A multipart/image send can have partially succeeded; do not replay it.
        with self._connect() as db:
            db.execute("UPDATE replies SET state='review' WHERE id=?", (ident,))

    @property
    def size(self):
        with self._connect() as db:
            return db.execute("SELECT count(*) FROM replies WHERE state='pending'").fetchone()[0]
