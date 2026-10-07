"""Workbench UI regressions: dialogs, read-only tree and preserved interview state."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from streamlit.testing.v1 import AppTest

APP_PATH=Path(__file__).resolve().parents[1]/'app.py'
sys.path.insert(0,str(APP_PATH.parent/'src'))
from wiki_intake import IntakeService
from wiki_store import approve_draft
from document_loader import load_uploaded_document


class FakeRun:
    def __init__(self,question=None,report=None):
        self.question=question
        self.calls=[]
        self.state={'turn_count':1 if report else 0,'max_turns':5,'interview_history':[],
                    'evaluation_history':[],'interview_report':report,'interview_status':'completed' if report else 'question_ready'}
    def resume(self,answer,on_progress=None): self.calls.append(answer)


class StreamlitPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();root=Path(self.tmp.name)
        self.service=IntakeService(root/'vault',root/'private')
        self.factory=patch('wiki_ui.IntakeService',return_value=self.service);self.factory.start()
        self.worker=patch('wiki_ui.ensure_worker');self.worker.start()
    def tearDown(self):
        self.worker.stop();self.factory.stop();self.tmp.cleanup()
    @staticmethod
    def button(page,key): return next(element for element in page.button if element.key==key)

    def test_initial_workbench_and_interview_dialog(self):
        page=AppTest.from_file(str(APP_PATH),default_timeout=10).run()
        self.assertFalse(page.exception)
        self.assertEqual(len(page.get('file_uploader')),0)
        self.assertEqual(len(page.chat_input),1)
        self.assertTrue(page.chat_input[0].disabled)
        self.button(page,'open-interview').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(len(page.get('file_uploader')),2)
        self.button(page,'wb-start-interview').click().run()
        self.assertTrue(any('同时上传' in error.value for error in page.error))
        self.assertEqual(self.service.queue.list(),[])

    def test_current_question_is_shown(self):
        page=AppTest.from_file(str(APP_PATH),default_timeout=10)
        page.session_state['interview_run']=FakeRun(question={'question':'TTL 过期时你会怎样处理？','technology':['Redis']})
        page.run();self.assertFalse(page.exception)
        self.assertTrue(any('TTL 过期时' in item.value for item in page.get('markdown')))
        self.assertFalse(page.chat_input[0].disabled)
        self.assertEqual(len(page.text_area),0)

    def test_final_report_is_shown(self):
        report={'overall_summary':'完成一次项目面试。','strengths':['清楚说明会话隔离'],
                'weaknesses':[{'topic':'TTL 策略','reason':'未说明续期','study_action':'比较固定与滑动过期'}],
                'next_practice_focus':['继续练习 Redis']}
        page=AppTest.from_file(str(APP_PATH),default_timeout=10)
        page.session_state['interview_run']=FakeRun(report=report);page.run()
        self.assertFalse(page.exception)
        self.assertIn('完成一次项目面试',' '.join(item.value for item in page.get('markdown')))
        self.assertTrue(any('TTL 策略' in item.label for item in page.get('expander')))

    def test_file_click_only_changes_read_only_preview(self):
        doc=load_uploaded_document('public.md',b'Original cache facts.')
        file=approve_draft(doc,{'title':'Cache note','body':'Read-only knowledge body.','tags':['Redis'],'links':[]},self.service.root)
        relative=file.path.relative_to(self.service.root).as_posix()
        run=FakeRun(question={'question':'你的会话怎样隔离？','technology':['Redis']})
        page=AppTest.from_file(str(APP_PATH),default_timeout=10);page.session_state['interview_run']=run
        page.run();self.button(page,'file-'+relative).click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.session_state['wiki_preview_file'],relative)
        self.assertEqual(run.calls,[])
        self.assertEqual(run.state['turn_count'],0)
        self.assertTrue(any('Read-only knowledge body' in item.value for item in page.get('markdown')))
        self.assertEqual(len(page.text_area),0)

    def test_library_expansion_is_a_separate_explicit_dialog(self):
        page=AppTest.from_file(str(APP_PATH),default_timeout=10).run()
        self.button(page,'open-knowledge-expansion').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(len(page.get('file_uploader')),1)
        self.button(page,'wiki_authorize_v4').click().run()
        self.assertTrue(any('添加' in item.value for item in page.error))
        self.assertEqual(self.service.queue.list(),[])


if __name__=='__main__': unittest.main()
