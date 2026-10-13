import unittest
from assistant.catalog import normalize_school_data,normalize_catalog
from assistant.browser import catalog_identity


def school_data():
    return {'batch':'fixture-turn','semester':'fixture-semester','type':'suitable-lessons',
        'pageInfo':{'currentPage':1,'totalRows':2,'totalPages':1},'lessons':[
            {'id':1001,'code':'TEST101.01','course':{'nameZh':'同名课','code':'TEST101','credits':2},
             'teachers':[{'nameZh':'同一教师'}],'limitCount':30,'enrolled':28,
             'dateTimePlace':{'textZh':'7~14周 星期一 10~11节 测试楼101;\n7~14周 星期三 10~11节 测试楼101'}},
            {'id':1002,'code':'TEST101.02','course':{'nameZh':'同名课','code':'TEST101','credits':2},
             'teachers':[{'nameZh':'同一教师'}],'limitCount':30,'enrolled':30,
             'dateTimePlace':{'textZh':'7~14周 星期二 10~11节 测试楼102'}}]}


class SchoolCatalogTests(unittest.TestCase):
    def normalize(self,data):
        return normalize_school_data(data,{'tables':[{'headers':['课程信息']}],'pagination':{'filtered':False}},catalog_identity)

    def test_current_school_fields_keep_same_name_teachers_distinct_and_full_seats(self):
        result=self.normalize(school_data())
        self.assertEqual(len(result['courses']),2)
        self.assertNotEqual(result['courses'][0].id,result['courses'][1].id)
        self.assertEqual([c.remaining for c in result['courses']],[2,0])
        self.assertEqual(result['coverage'],'complete')
        self.assertEqual(len(result['courses'][0].meetings),2)

    def test_counts_change_preserves_id_and_semester_change_does_not(self):
        data=school_data();first=self.normalize(data)['courses'][0]
        data['lessons'][0]['enrolled']=29
        self.assertEqual(first.id,self.normalize(data)['courses'][0].id)
        data['semester']='next-semester'
        self.assertNotEqual(first.id,self.normalize(data)['courses'][0].id)

    def test_filtered_and_partial_pages_cannot_claim_complete_catalog(self):
        data=school_data();data['filtered']=True
        self.assertEqual(self.normalize(data)['coverage'],'current_page')
        data['filtered']=False;data['pageInfo']['totalRows']=10
        self.assertEqual(self.normalize(data)['coverage'],'current_page')

    def test_unknown_schedule_and_duplicate_ids_are_not_hidden(self):
        data=school_data();data['lessons'][0]['dateTimePlace']['textZh']='尚未公布'
        self.assertFalse(self.normalize(data)['courses'][0].schedule_known)
        data['lessons'][1]['id']=1001
        with self.assertRaises(ValueError):self.normalize(data)

    def test_full_school_display_keeps_all_columns_and_all_time_place_lines(self):
        data=school_data()
        display={'verified':True,'operation':'选课','status':'待选课',
            'info':'同名课\nTEST101|测试学院|理论课\n2学分|32学时|公选|选修|中文授课|考试',
            'module':'测试课程模块','lesson':'TEST101.01\n测试专业限制','teachers':'同一教师',
            'time_place':'测试校区\n7~14周 星期一 10~11节 测试楼101;\n7~14周 星期三 10~11节 测试楼101','enrollment':'28/30'}
        data['lessons'][0]['schoolDisplay']=display
        course=self.normalize(data)['courses'][0]
        self.assertEqual(course.public()['school_display'],display)
        self.assertEqual(len(course.meetings),2)
