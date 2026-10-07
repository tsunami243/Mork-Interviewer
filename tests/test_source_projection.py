"""A source summary and its immutable parsed snapshot form one visible source node."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from wiki_relations import graph_data,source_aliases,relation_cards
from wiki_details import file_listing,file_details

class SourceProjectionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name);self.hash='a'*24
  self.raw=f'sources/user-{self.hash}.md';self.summary=f'sources/source-{self.hash}.md'
 def tearDown(self):self.tmp.cleanup()
 def write(self,path,text):
  p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8');return p
 def setup_source(self):
  self.write(self.raw,'# 原始资料快照\n\n文件：示例.docx\n\nOriginal facts.')
  self.write(self.summary,f'---\nid: source-{self.hash}\ntitle: 资料摘要：示例.docx\ntype: source\nstatus: approved\nsources: [{self.raw}]\n---\n\n# Summary\n[[concepts/example]]')
  self.write('concepts/example.md',f'---\ntitle: Concept\nsources: [{self.raw}]\n---\n# Concept\n[[source-{self.hash}]]\n[[sources/user-{self.hash}]]')
 def test_duplicate_source_nodes_and_edges_are_folded_without_deleting_files(self):
  self.setup_source();before={p:p.read_bytes() for p in self.root.rglob('*.md')};graph=graph_data(self.root)
  self.assertEqual(source_aliases(self.root),{self.raw:self.summary})
  self.assertEqual({n['path'] for n in graph['nodes']},{self.summary,'concepts/example.md'})
  self.assertEqual(len(graph['links']),1);self.assertEqual(len(graph['links'][0]['directions']),2)
  self.assertTrue(all(e['source']!=e['target'] for e in graph['links']))
  self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*.md')})
 def test_library_has_one_source_but_original_remains_readable(self):
  self.setup_source();paths={f['path'] for f in file_listing(self.root)}
  self.assertIn(self.summary,paths);self.assertNotIn(self.raw,paths)
  detail=file_details(self.root,self.summary);self.assertEqual(detail['original_sources'][0]['path'],self.raw)
  original=file_details(self.root,self.raw);self.assertIn('Original facts.',original['body']);self.assertEqual(original['source_summary']['path'],self.summary)
  cards=relation_cards(self.root,'concepts/example.md');self.assertEqual(len(cards),1);self.assertEqual(cards[0]['path'],self.summary)
 def test_same_title_does_not_merge_unrelated_or_unapproved_files(self):
  self.setup_source();other=self.write('sources/other.md','# 示例.docx\nOther source.')
  self.assertIn('sources/other.md',{n['path'] for n in graph_data(self.root)['nodes']})
  text=(self.root/self.summary).read_text();(self.root/self.summary).write_text(text.replace('status: approved','status: draft'),encoding='utf-8')
  self.assertEqual(source_aliases(self.root),{});self.assertIn(self.raw,{n['path'] for n in graph_data(self.root)['nodes']})

if __name__=='__main__':unittest.main()
