import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from document_loader import load_uploaded_document
from wiki_intake import IntakeService, document_metadata, split_source
from wiki_actions import WikiActions
from wiki_relations import graph_data
from wiki_store import _frontmatter


class WeakLLM:
    def __init__(self, target):
        self.target = target
        self.calls = []

    def invoke(self, messages):
        payload = json.loads(messages[-1].content)
        self.calls.append(payload)
        if payload.get('mode') == 'weak_relink':
            return SimpleNamespace(content=json.dumps({'links': [{
                'target': self.target,
                'type': 'reference',
                'reason': '两页都讨论缓存过期，但不足以证明它们描述同一实现。',
                'source_quote': '孤立资料讨论缓存过期策略。',
                'target_quote': '目标知识说明缓存过期策略。',
            }]}, ensure_ascii=False))
        raise AssertionError('unexpected model mode')


class EntityAndWeakTests(unittest.TestCase):
    def test_filename_is_traceable_entity_evidence(self):
        document = load_uploaded_document('美团面经.md', '面试讨论了缓存策略。'.encode())
        content = '面试讨论了缓存策略。'
        data = {
            'content': content,
            'document': {'source': document.source, 'segments': [
                {'text': content, 'page': None}]},
            'document_metadata': document_metadata(document, content),
            'rules': {}, 'context': {'catalog': [], 'pages': []},
            'stage': 'core', 'chunks': split_source(content),
        }

        class Model:
            def invoke(self, messages):
                return SimpleNamespace(content=json.dumps({
                    'summary': '缓存策略。',
                    'topics': [{
                        'title': '美团', 'type': 'entity', 'entity_kind': 'company',
                        'page_worthy': True, 'aliases': ['Meituan'],
                        'entity_basis': ['filename'], 'identity_confidence': 'high',
                        'metadata_evidence': ['文件名：美团面经.md'],
                        'tags': ['美团'], 'key_points': ['讨论缓存策略。'],
                        'quotes': ['面试讨论了缓存策略。'], 'existing_page_id': None,
                        'related_ids': [], 'conflicts': [],
                    }],
                }, ensure_ascii=False))

        analysis = IntakeService().analyze(Model(), data['chunks'][0], data)
        from wiki_pipeline import qualified_topics
        self.assertEqual([topic['title'] for topic in qualified_topics(analysis['topics'], data)], ['美团'])
        self.assertEqual(analysis['topics'][0]['entity_basis'], ['filename'])

    def test_weak_scan_is_review_only_until_confirmed(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            service = IntakeService(base / 'vault', base / 'private')
            source = service.root / 'sources' / 'seed.md'
            source.parent.mkdir(parents=True)
            source.write_text('# seed\n', encoding='utf-8')
            def page(path, title, body, newline='\n'):
                target = service.root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                content = _frontmatter({
                    'id': target.stem, 'title': title, 'type': 'concept', 'tags': ['Redis'],
                    'sources': ['sources/seed.md'], 'status': 'approved', 'evidence': [],
                    'aliases': [], 'relations': [],
                }) + f'# {title}\n\n{body}\n'
                target.write_bytes(content.replace('\n', newline).encode('utf-8'))
            page('concepts/isolated.md', '孤立缓存', '孤立资料讨论缓存过期策略。', newline='\r\n')
            page('concepts/target.md', '缓存过期', '目标知识说明缓存过期策略。')
            model = WeakLLM('concepts/target.md')
            actions = WikiActions(service, lambda: model, lambda: None)
            try:
                before = (service.root / 'concepts/isolated.md').read_bytes()
                task = actions.start_weak_scan('owner')
                for _ in range(200):
                    row = actions.get(task['id'])
                    if row['status'] in ('review', 'failed'):
                        break
                    sleep(0.01)
                self.assertEqual(row['status'], 'review', row['error'])
                self.assertEqual((service.root / 'concepts/isolated.md').read_bytes(), before)
                suggestion = row['payload']['suggestions'][0]
                self.assertEqual(suggestion['relation']['origin'], 'weak_relink')
                actions.apply_links('owner', task['id'], [suggestion['id']])
                for _ in range(200):
                    row = actions.get(task['id'])
                    if row['status'] in ('completed', 'failed'):
                        break
                    sleep(0.01)
                self.assertEqual(row['status'], 'completed', row['error'])
                text = (service.root / 'concepts/isolated.md').read_text(encoding='utf-8')
                self.assertIn('[[concepts/target]]', text)
                self.assertIn('weak_relink', text)
                self.assertTrue(any('弱引用' in kind for edge in graph_data(service.root)['links'] for kind in edge['types']))
            finally:
                actions.pool.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    unittest.main()
