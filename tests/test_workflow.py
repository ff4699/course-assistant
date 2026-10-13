import unittest
from assistant.workflow import guide,run_summary
from assistant.models import Plan,analyze_plan
from assistant.demo import sample_courses,required_courses

class WorkflowTests(unittest.TestCase):
    def test_unknown_existing_time_never_ticks_conflicts_with_full_import(self):
        catalog=sample_courses();existing=required_courses();existing[0].schedule_known=False
        plan=Plan(course_ids=['demo:photo-a']);analysis=analyze_plan(plan,catalog,existing)
        steps=guide('school',{'state':'connected','message':'正常登录'},catalog,existing,plan,analysis,'complete',{'state':'success'},{'state':'success','coverage':'complete','context_verified':True},{'phase':'idle','targets':[]})
        by_id={s['id']:s for s in steps}
        self.assertEqual(by_id['login']['state'],'done');self.assertEqual(by_id['timetable']['state'],'done')
        self.assertNotEqual(by_id['conflicts']['state'],'done');self.assertEqual(by_id['execute']['state'],'unavailable')
    def test_unknown_results_not_reported_successful(self):
        summary=run_summary({'phase':'finished','elapsed_ms':700,'targets':[{'status':'succeeded','attempts':1},{'status':'unconfirmed','attempts':1}]})
        self.assertEqual(summary['successful'],1);self.assertEqual(summary['unsuccessful'],1);self.assertIn('未完成',summary['feedback'])
