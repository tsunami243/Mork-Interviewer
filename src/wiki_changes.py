"""Persistent, debounced detection of externally added Markdown. Never calls models."""
import json
import time
from wiki_relations import documents
from wiki_publish import file_digest


class ChangeTracker:
    def __init__(self, service):
        self.service = service
        with service.queue.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS wiki_observed(vault TEXT,path TEXT,hash TEXT,stable_since REAL,state TEXT,PRIMARY KEY(vault,path))')
            db.execute('CREATE TABLE IF NOT EXISTS wiki_tracker(vault TEXT PRIMARY KEY,initialized INTEGER)')
            exists = db.execute('SELECT 1 FROM wiki_tracker WHERE vault=?',(service.queue.vault,)).fetchone()
            if not exists:
                for path in documents(service.root):
                    db.execute('INSERT OR IGNORE INTO wiki_observed VALUES(?,?,?,?,?)',
                        (service.queue.vault,path,file_digest(service.root/path),time.time(),'baseline'))
                db.execute('INSERT INTO wiki_tracker VALUES(?,1)',(service.queue.vault,))

    def generated_paths(self):
        paths = set()
        with self.service.queue.connection() as db:
            rows = db.execute("SELECT payload FROM jobs WHERE vault=? AND status IN ('completed','publishing')",(self.service.queue.vault,)).fetchall()
            for row in rows:
                data = json.loads(row['payload'])
                paths.update(data.get('receipt',{}).get('changed_paths',[]))
                if data.get('proposal'):
                    paths.update(p['path'] for p in data['proposal']['pages'])
                    paths.add(data['proposal']['source_path'])
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wiki_actions'").fetchone():
                for row in db.execute('SELECT payload FROM wiki_actions WHERE vault=?',(self.service.queue.vault,)):
                    paths.update(json.loads(row['payload']).get('receipt',{}).get('changed_paths',[]))
        return paths

    def scan(self, *, now=None, settle=2):
        if (self.service.root/'.wiki-publishing.json').exists(): return []
        now = time.time() if now is None else now
        docs = documents(self.service.root); generated = self.generated_paths()
        pending = []
        with self.service.queue.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for path, doc in docs.items():
                value = file_digest(self.service.root/path)
                old = db.execute('SELECT * FROM wiki_observed WHERE vault=? AND path=?',(self.service.queue.vault,path)).fetchone()
                internal = path in generated or path in ('index.md','overview.md','log.md','schema.md','purpose.md')
                if old is None:
                    db.execute('INSERT INTO wiki_observed VALUES(?,?,?,?,?)',
                        (self.service.queue.vault,path,value,now,'internal' if internal else 'settling'))
                    continue
                if old['hash']!=value:
                    # Existing baseline/internal pages are not mistaken for new file imports.
                    state = 'settling' if old['state'] in ('settling','pending','analyzing','review','failed') else old['state']
                    db.execute('UPDATE wiki_observed SET hash=?,stable_since=?,state=? WHERE vault=? AND path=?',
                        (value,now,state,self.service.queue.vault,path))
                    continue
                state = old['state']
                if internal: state='internal'
                elif state=='settling' and now-old['stable_since']>=settle: state='pending'
                if state!=old['state']:
                    db.execute('UPDATE wiki_observed SET state=? WHERE vault=? AND path=?',(state,self.service.queue.vault,path))
                if state in ('pending','failed'):
                    pending.append({'path':path,'title':doc['title'],'hash':value})
            # Missing files are not offered, and replacing a deleted file is a new import.
            for row in db.execute('SELECT path FROM wiki_observed WHERE vault=?',(self.service.queue.vault,)).fetchall():
                if row['path'] not in docs:
                    db.execute('DELETE FROM wiki_observed WHERE vault=? AND path=?',(self.service.queue.vault,row['path']))
        return pending

    def state(self, paths, state):
        with self.service.queue.connection() as db:
            for path in paths:
                db.execute('UPDATE wiki_observed SET state=? WHERE vault=? AND path=?',(state,self.service.queue.vault,path))

    def checked(self, paths):
        pending = {p['path']:p for p in self.scan()}
        if not paths or len(paths)>20 or any(path not in pending for path in paths):
            raise ValueError('新增文件不存在、尚未写入稳定或正在分析')
        return [pending[path] for path in dict.fromkeys(paths)]
