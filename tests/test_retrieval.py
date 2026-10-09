"""Evidence validation and model-boundary regression tests."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import UUID

from pipeline.models import ModelUnavailable
from pipeline.retrieval import answer_question

DEV='11111111-1111-4111-8111-111111111111'
PROD='22222222-2222-4222-8222-222222222222'
CHUNK='33333333-3333-4333-8333-333333333333'
SOURCE='44444444-4444-4444-8444-444444444444'
UNKNOWN='55555555-5555-4555-8555-555555555555'
PRIOR='66666666-6666-4666-8666-666666666666'
NOW=datetime(2026,10,1,tzinfo=timezone.utc)

class Models:
    def __init__(self, selected=None, cited=None, answer=None, by_cell=None):
        self.selected=[CHUNK] if selected is None else selected
        self.cited=[CHUNK] if cited is None else cited
        self.answer=answer or f'Dev has observed timeouts [{CHUNK}]. Prod coverage is missing.'
        self.contexts=[]
        self.by_cell=by_cell
    async def embed(self,text):return [0.0]*768
    async def generate(self,instruction,context,schema):
        self.contexts.append(context)
        if 'selected_ids' in schema['properties']:return {'selected_ids':self.selected}
        return {'answer':self.answer,'evidence_by_cell':self.by_cell if self.by_cell is not None else {'dev / current':self.cited[0]}}

class QueryBoundaryTests(IsolatedAsyncioTestCase):
    async def query(self,models,both_periods=False,missing_prod=False):
        body=SimpleNamespace(question='Compare dev and prod timeouts' if missing_prod else 'Compare dev timeouts',environment_ids=[UUID(DEV),UUID(PROD)],timezone='UTC',start=NOW-timedelta(hours=1),end=NOW,compare_start=None,compare_end=None,service=None)
        if both_periods:body.compare_start=NOW-timedelta(days=30,hours=1);body.compare_end=NOW-timedelta(days=30)
        class Conn:
            def execute(self,*args):return self
            def fetchall(self):return [{'id':UUID(DEV),'name':'dev'},{'id':UUID(PROD),'name':'prod'}]
        @contextmanager
        def db(*args):yield Conn()
        def candidates(conn,project,env,window,*args):
            if env==PROD:return []
            return [{'id':CHUNK if window['label']=='current' else PRIOR,'source_id':SOURCE,'environment_id':DEV,'environment':'dev','bucket_start':window['start'],'bucket_end':window['end'],'service':'api','level':'error','release':'v2','summary':'Observed request timeout','event_count':2,'cell':DEV+':'+window['label']}]
        def coverage(conn,project,env,window,*args):
            return {'complete':env==DEV,'sources':[{'source_id':SOURCE,'complete':True}], 'observed_events':2 if env==DEV else 0,'measured_events':0,'observed_mean_duration_ms':None,'notes':'Observed only.'}
        with patch('pipeline.retrieval.database',db),patch('pipeline.retrieval.project_access'),patch('pipeline.retrieval.candidates_for',candidates),patch('pipeline.retrieval.coverage_for',coverage):
            return await answer_question('owner','project',body,models)

    async def test_missing_environment_remains_a_gap_and_internal_ids_do_not_reach_models(self):
        models=Models();result=await self.query(models,missing_prod=True)
        self.assertTrue(any('prod' in gap for gap in result['gaps']))
        self.assertEqual(result['cited_evidence_ids'],[CHUNK])
        self.assertEqual([row['id'] for row in result['evidence']],[CHUNK])
        for internal in (DEV,PROD,SOURCE):self.assertNotIn(internal,str(models.contexts))
        self.assertIn(CHUNK,str(models.contexts))

    async def test_unknown_remapper_reference_stops_generation(self):
        models=Models(selected=[UNKNOWN])
        with self.assertRaises(ModelUnavailable):await self.query(models)
        self.assertEqual(len(models.contexts),1)

    async def test_unknown_answer_reference_is_rejected(self):
        with self.assertRaises(ModelUnavailable):await self.query(Models(cited=[UNKNOWN]))

    async def test_uncited_inline_reference_is_rejected(self):
        with self.assertRaises(ModelUnavailable):await self.query(Models(answer='Unsupported reference '+UNKNOWN))

    async def test_invalid_configuration_cannot_silently_remove_a_comparison_cell(self):
        with patch.dict('os.environ',{'LOGCHAT_RETRIEVAL_CANDIDATES':'2','LOGCHAT_CONTEXT_PER_CELL':'4'}):
            from pipeline.retrieval import QueryError
            with self.assertRaises(QueryError):await self.query(Models())

    async def test_configured_one_chunk_budget_preserves_missing_environment_disclosure(self):
        with patch.dict('os.environ',{'LOGCHAT_RETRIEVAL_CANDIDATES':'1','LOGCHAT_CONTEXT_PER_CELL':'1','LOGCHAT_CONTEXT_BUDGET':'1024'}):
            result=await self.query(Models(),missing_prod=True)
            self.assertEqual(len(result['evidence']),1)
            self.assertTrue(any('prod' in gap for gap in result['gaps']))

    async def test_comparison_requires_citation_for_each_selected_period(self):
        models=Models(selected=[CHUNK,PRIOR],by_cell={'dev / current':CHUNK})
        with self.assertRaises(ModelUnavailable):await self.query(models,both_periods=True)

    async def test_known_citation_cannot_be_assigned_to_the_wrong_period(self):
        models=Models(selected=[CHUNK,PRIOR],by_cell={'dev / current':PRIOR,'dev / previous':CHUNK})
        with self.assertRaises(ModelUnavailable):await self.query(models,both_periods=True)

    async def test_missing_inline_comparison_citation_gets_deterministic_labelled_reference(self):
        models=Models(selected=[CHUNK,PRIOR],by_cell={'dev / current':CHUNK,'dev / previous':PRIOR},answer=f'Current period had timeouts [{CHUNK}].')
        result=await self.query(models,both_periods=True)
        self.assertIn('dev / previous ['+PRIOR+']',result['answer'])
        self.assertEqual(set(result['cited_evidence_ids']),{CHUNK,PRIOR})

    async def test_missing_side_cannot_be_filled_by_a_fabricated_model_comparison(self):
        models=Models(answer="Both dev and prod have identical performance.")
        result=await self.query(models,missing_prod=True)
        self.assertEqual(len(models.contexts),1)
        self.assertNotIn("identical",result['answer'])
        self.assertIn("prod / current: no relevant evidence; its behavior is unknown.",result['answer'])
        self.assertIn("dev / current: 2 observed events",result['answer'])
