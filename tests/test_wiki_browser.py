"""Safe knowledge tree and preview cannot modify files or read outside the vault."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from wiki_browser import catalog,preview,safe_file
from wiki_store import list_pages


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)/'wiki';self.root.mkdir()
    def tearDown(self): self.tmp.cleanup()
    def write(self,relative,text):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8');return path

    def test_tree_shows_real_hierarchy_and_read_only_titles(self):
        self.write('index.md','# Project Overview')
        self.write('entities/company.md','---\ntitle: Company\n---\n# Company facts')
        path=self.write('concepts/java/thread.md','# Java线程\n\nExisting details.')
        before=path.read_bytes()
        files=catalog(self.root)
        self.assertEqual({file.group for file in files},{'概览','实体','概念'})
        self.assertIn('concepts/java/thread.md',[file.relative for file in files])
        item,body,truncated=preview(self.root,'concepts/java/thread.md')
        self.assertEqual(item.title,'Java线程');self.assertIn('Existing details',body)
        self.assertFalse(truncated);self.assertEqual(path.read_bytes(),before)

    def test_plain_viewable_note_is_not_implicitly_indexed(self):
        self.write('concepts/note.md','# Local note\nNot published.')
        self.assertEqual(len(catalog(self.root)),1)
        self.assertEqual(list_pages(self.root),[])

    def test_private_and_traversal_paths_are_rejected(self):
        self.write('.env','private')
        for relative in ('.env','../secret.md','concepts/../../secret.md','private/keys.md'):
            with self.subTest(relative=relative),self.assertRaises(ValueError): safe_file(self.root,relative)

    def test_remote_images_are_not_loaded_and_original_is_unchanged(self):
        text='# Note\n![secret](https://outside.test/image.png)\n'
        path=self.write('concepts/view.md',text);before=path.read_bytes()
        _,body,_=preview(self.root,'concepts/view.md')
        self.assertNotIn('![secret]',body)
        self.assertNotIn('https://outside',body)
        self.assertEqual(path.read_bytes(),before)

    def test_draft_is_not_shown_as_library_document(self):
        self.write('concepts/draft.md','---\nid: draft\nstatus: draft\ntitle: Draft\n---\ntext')
        self.assertEqual(catalog(self.root),[])


if __name__=='__main__': unittest.main()
