"""Real browser UI smoke test against an isolated, temporary demo app.

Run: python tests/browser_smoke.py
No school connection or real enrollment is performed.
"""
import json
from datetime import datetime,timezone,timedelta
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as temp:
        with socket.socket() as port_socket:
            port_socket.bind(('127.0.0.1', 0))
            port = port_socket.getsockname()[1]
        env = {**os.environ, 'COURSE_STATE_DIR': str(Path(temp) / 'state')}
        binary = sys.executable
        if os.name == 'nt' and sys.prefix != sys.base_prefix:
            binary = sys._base_executable
            env['PYTHONPATH'] = sysconfig.get_path('purelib') + os.pathsep + env.get('PYTHONPATH', '')
        server = subprocess.Popen([binary, '-m', 'assistant', '--port', str(port)],
                                  cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            url = f'http://127.0.0.1:{port}/'
            for _ in range(100):
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200: break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError('Isolated app did not start')
            errors = []
            options = {'headless': True}
            binary = os.environ.get('COURSE_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
            if binary: options['executable_path'] = binary
            if os.name != 'nt': options['args'] = ['--no-sandbox']
            with sync_playwright() as p:
                browser = p.chromium.launch(**options)
                page = browser.new_page(viewport={'width':1440, 'height':1100})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url, wait_until='networkidle')
                expect(page.locator('#guideDialog')).to_be_visible()
                expect(page.locator('#setupGuide li')).to_have_count(7)
                page.keyboard.press('Escape')
                expect(page.locator('.sidebar .nav-item')).to_have_count(3)
                page.locator('#pageSize').select_option('20')
                expect(page.locator('#courseRows tr')).to_have_count(9)
                expect(page.locator('#setupGuide li')).to_have_count(7)
                expect(page.locator('.brand')).to_have_attribute('aria-label','课程助手')
                assert page.request.get(url+'static/university-logo.png').status==404
                page.locator('#aboutButton').click()
                expect(page.locator('#aboutDialog')).to_be_visible()
                expect(page.locator('#aboutDialog')).to_contain_text('Playwright')
                expect(page.locator('#aboutDialog')).to_contain_text('校区25级机械某不知名发烧友')
                expect(page.locator('#aboutDialog')).not_to_contain_text('yx大人')
                assert page.locator('.about-author').evaluate('e=>getComputedStyle(e).fontSize')=='11px'
                page.locator('#aboutClose').click()
                page.evaluate('window.scrollTo(0,0)')
                page.locator('#settingsButton').click()
                expect(page.locator('#retries')).to_have_value('3')
                page.locator('#retries').fill('10')
                page.locator('#settingsForm button[type="submit"]').click()
                expect(page.locator('#settingsDialog')).not_to_be_visible()
                page.reload(wait_until='networkidle')
                expect(page.locator('#guideDialog')).not_to_be_visible()
                page.locator('#settingsButton').click()
                expect(page.locator('#retries')).to_have_value('10')
                page.locator('#retries').fill('11')
                page.locator('#settingsForm button[type="submit"]').click()
                expect(page.locator('#settingsDialog')).to_be_visible()
                assert page.request.get(url+'api/state?mode=demo').json()['plan']['retries']==10
                page.locator('#retries').fill('3')
                page.locator('#settingsForm button[type="submit"]').click()
                expect(page.locator('#settingsDialog')).not_to_be_visible()
                page.locator('[data-course="demo:ai"]').check()
                expect(page.locator('#conflictCount')).to_have_text('1')
                expect(page.locator('#startRun')).to_be_disabled()
                page.locator('[data-course="demo:ai"]').uncheck()
                # A planned pair must be checked even when neither conflicts with required courses.
                page.locator('[data-course="demo:photo-a"]').check()
                page.locator('[data-course="demo:city"]').check()
                expect(page.locator('#planIssues')).to_contain_text('计划之间')
                expect(page.locator('#startRun')).to_be_disabled()
                page.locator('.nav-item[data-page="schedule"]').click()
                page.locator('#weekSelect').select_option('1')
                expect(page.locator('.timetable-period')).to_have_count(12)
                expect(page.locator('.timetable-period').first).to_contain_text('09:30')
                expect(page.locator('.timetable-period').last).to_contain_text('22:55')
                expect(page.locator('.timetable-event.conflict')).to_have_count(2)
                positions = page.locator('.timetable-event.conflict').evaluate_all('es=>es.map(e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right}})')
                assert positions[0]['right'] <= positions[1]['left'] or positions[1]['right'] <= positions[0]['left'], positions
                page.locator('.timetable-event.existing').filter(has_text='高等数学').click()
                expect(page.locator('#courseDetail')).to_contain_text('09:30–11:05')
                page.locator('#closeCourse').click()
                page.locator('#showPlanned').uncheck()
                expect(page.locator('.timetable-event.planned')).to_have_count(0)
                expect(page.locator('.timetable-event.existing')).to_have_count(2)
                page.locator('#showPlanned').check()
                page.locator('.nav-item[data-page="plan"]').click()
                page.locator('[data-course="demo:city"]').uncheck()
                page.locator('[data-course="demo:ethics"]').check()
                expect(page.locator('#conflictCount')).to_have_text('0')
                page.locator('.nav-item[data-page="schedule"]').click()
                page.locator('#weekSelect').select_option('17')
                expect(page.locator('.timetable-event')).to_have_count(1)
                expect(page.locator('.timetable-event')).to_contain_text('工程伦理')
                page.locator('.nav-item[data-page="plan"]').click()
                page.locator('#samplePlan').click()
                expect(page.locator('#chosenCount')).to_have_text('4')
                expect(page.locator('#startRun')).to_be_enabled()
                page.locator('#pageSize').select_option('auto')
                page.evaluate('document.getElementById("toast").hidden=true;document.querySelectorAll(".table-scroll").forEach(e=>e.scrollTop=0)')
                page.screenshot(path=str(ROOT/'docs/preview.png'), full_page=True)
                page.locator('.nav-item[data-page="schedule"]').click()
                page.locator('#weekSelect').select_option('1')
                expect(page.locator('.timetable-event')).to_have_count(6)
                expect(page.locator('.timetable-event.planned').filter(has_text='摄影基础')).to_contain_text('16:50–18:40')
                page.screenshot(path=str(ROOT/'docs/timetable-preview.png'), full_page=True)
                page.locator('.nav-item[data-page="plan"]').click()
                page.locator('#startRun').click()
                expect(page.locator('.run-card .tag.succeeded')).to_have_count(4, timeout=15000)
                expect(page.locator('#runPhase')).to_have_text('演练结束')
                expect(page.locator('#runSummary')).to_contain_text('成功 4 门')
                expect(page.locator('#runSummary')).to_contain_text('课表已更新')
                expect(page.locator('#runTargets')).to_contain_text('响应')
                measured=page.request.get(url+'api/state?mode=demo').json()['run']
                assert measured['summary']['elapsed_ms']>0
                assert all(t.get('submission_ms',-1)>=0 and t.get('verification_ms',-1)>=0 for t in measured['targets'])
                expect(page.locator('#runTargets .run-card').filter(has_text='电影与文化')).to_contain_text('提交 2 次')
                expect(page.locator('#workflowTitle')).to_contain_text('4/4')
                page.screenshot(path=str(ROOT/'docs/execution-preview.png'), full_page=True)
                page.locator('.nav-item[data-page="monitor"]').click()
                expect(page.locator('#runTargets .tag.succeeded')).to_have_count(4)
                assert not page.locator('#browserViewDetails').evaluate('e=>e.open')
                page.locator('#browserViewDetails > summary').click()
                expect(page.locator('#viewerEmpty')).to_be_visible()
                page.screenshot(path=str(ROOT/'docs/monitor-preview.png'), full_page=True)
                page.locator('.nav-item[data-page="monitor"]').click()
                page.locator('#page-logs').evaluate('e=>e.open=true')
                expect(page.locator('#eventList')).to_contain_text('课表')
                page.locator('#mode').select_option('school')
                page.locator('#openConnection').click()
                expect(page.locator('#sessionTitle')).to_have_text('尚未连接')
                expect(page.locator('#loginArea')).to_be_hidden()
                page.locator('#importFile').set_input_files(str(ROOT/'examples/school-courses.json'))
                expect(page.locator('#catalogCount')).to_have_text('1')
                page.locator('.nav-item[data-page="plan"]').click()
                expect(page.locator('#courseRows')).to_contain_text('示例课程')
                expect(page.locator('#conflictCount')).to_have_text('—')
                expect(page.locator('#startRun')).to_be_disabled()
                page.locator('#openPlan').click()
                with page.expect_download() as download_info:
                    page.locator('#export').click()
                page.keyboard.press('Escape')
                export_path = Path(temp)/'export.json'
                download_info.value.save_as(export_path)
                assert set(json.loads(export_path.read_text(encoding='utf-8'))) == {'catalog','existing','plan','coverage','run','calendar'}
                page.locator('#mode').select_option('demo')
                page.locator('.nav-item[data-page="monitor"]').click()
                page.locator('#page-logs').evaluate('e=>e.open=true')
                page.locator('#resetDemo').click()
                expect(page.locator('#eventList')).to_contain_text('还没有执行记录')
                # Stop and continue stay available on the results page.
                saved_plan=page.request.get(url+'api/state?mode=demo').json()['plan']
                queued_plan={**saved_plan,'start_at':(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()}
                response=page.request.put(url+'api/plan?mode=demo',data=queued_plan,headers={'X-Course-Client':'local'})
                assert response.ok,response.text()
                page.reload(wait_until='networkidle')
                page.locator('#startRun').click()
                expect(page.locator('#runPhase')).to_have_text('等待开始')
                expect(page.locator('#stopRun')).to_be_visible()
                page.locator('#stopRun').click()
                expect(page.locator('#runPhase')).to_have_text('已停止')
                expect(page.locator('#resumeRun')).to_be_visible()
                page.locator('#resumeRun').click()
                expect(page.locator('#runTargets .tag.succeeded')).to_have_count(4,timeout=15000)
                response=page.request.put(url+'api/plan?mode=demo',data=saved_plan,headers={'X-Course-Client':'local'})
                assert response.ok
                page.locator('#page-logs > summary').click()
                page.locator('#resetDemo').click()
                expect(page.locator('#eventList')).to_contain_text('还没有执行记录')
                page.locator('.nav-item[data-page="plan"]').click()
                page.set_viewport_size({'width':390,'height':844})
                expect(page.locator('#tableSummary')).to_contain_text('横向滑动')
                page.evaluate('document.getElementById("toast").hidden=true')
                page.screenshot(path=str(ROOT/'docs/mobile-preview.png'), full_page=True)
                dimensions = page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
                assert dimensions['scroll'] <= dimensions['width'], dimensions
                page.locator('.nav-item[data-page="schedule"]').click()
                page.locator('#weekSelect').select_option('1')
                scroll = page.locator('.timetable-scroll').evaluate('e=>({client:e.clientWidth,scroll:e.scrollWidth})')
                assert scroll['scroll'] > scroll['client'], scroll
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(ROOT/'docs/mobile-timetable-preview.png'), full_page=True)
                # Theme follows the icon and survives reload; the workspace fills
                # wide screens and CSS-width changes produced by browser zoom.
                page.locator('.nav-item[data-page="plan"]').click()
                page.locator('#pageSize').select_option('auto')
                for width in (2560, 1920, 1280, 960, 390):
                    page.set_viewport_size({'width':width,'height':1000})
                    page.wait_for_timeout(100)
                    geometry=page.locator('.workspace').evaluate('e=>({right:e.getBoundingClientRect().right,scroll:document.documentElement.scrollWidth,width:innerWidth})')
                    assert abs(geometry['right']-width)<=1, geometry
                    assert geometry['scroll']<=width, geometry
                    if width>980:
                        dense=page.evaluate("""() => {
                            const c=document.querySelector('.catalog-panel').getBoundingClientRect();
                            const s=document.querySelector('#planBar').getBoundingClientRect();
                            const table=document.querySelector('.catalog-panel .table-scroll').getBoundingClientRect();
                            const row=document.querySelector('#courseRows tr:last-child').getBoundingClientRect();
                            return {width:Math.abs(c.width-s.width),below:s.top>=c.bottom,
                                rowBottom:row.bottom,tableBottom:table.bottom,
                                footer:s.bottom+parseFloat(getComputedStyle(document.querySelector('.workspace')).paddingBottom),
                                height:innerHeight,scroll:document.documentElement.scrollHeight};
                        }""")
                        assert dense['width']<=1 and dense['below'], dense
                        assert dense['rowBottom']<=dense['tableBottom']+1, dense
                        assert abs(dense['footer']-dense['height'])<=1 and dense['scroll']<=dense['height'], dense
                for width,height in ((1536,864),(1280,720)):
                    page.set_viewport_size({'width':width,'height':height})
                    page.wait_for_timeout(100)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.set_viewport_size({'width':1920,'height':1080})
                page.locator('#themeToggle').click()
                expect(page.locator('html')).to_have_attribute('data-theme','dark')
                expect(page.locator('#themeToggle')).to_have_text('☾')
                page.reload(wait_until='networkidle')
                expect(page.locator('html')).to_have_attribute('data-theme','dark')
                assert page.locator('.panel').first.evaluate('e=>getComputedStyle(e).backgroundColor')=='rgb(24, 28, 35)'
                page.screenshot(path=str(ROOT/'docs/dark-preview.png'), full_page=True)
                page.locator('#themeToggle').click()
                expect(page.locator('#themeToggle')).to_have_text('☀')
                # A realistic number of teaching classes exercises page jumps,
                # filtering, and preserved choices across saved-directory pages.
                catalog=[{'id':f'school:fixture:{i}','name':f'分页测试课程{i:02}', 'class_code':f'CLASS{i:02}',
                    'teachers':['分页测试教师'], 'source':'school', 'semester':'unverified', 'schedule_known':False}
                    for i in range(1,48)]
                page.locator('#openConnection').click()
                page.locator('#importFile').set_input_files({'name':'fixture.json','mimeType':'application/json',
                    'buffer':json.dumps({'catalog':catalog},ensure_ascii=False).encode('utf-8')})
                expect(page.locator('#catalogCount')).to_have_text('47')
                page.locator('.nav-item[data-page="plan"]').click()
                page.locator('#pageSize').select_option('20')
                expect(page.locator('#courseRows tr')).to_have_count(20)
                page.locator('[data-course="school:fixture:1"]').check()
                page.locator('#pageJump').fill('3')
                page.locator('#pageJumpForm button').click()
                expect(page.locator('#courseRows tr')).to_have_count(7)
                expect(page.locator('#catalogPageLabel')).to_have_text('3 / 3')
                expect(page.locator('#chosenCourses')).to_contain_text('分页测试课程01')
                page.locator('#search').fill('分页测试课程01')
                expect(page.locator('#courseRows tr')).to_have_count(1)
                expect(page.locator('#catalogPageLabel')).to_have_text('1 / 1')
                page.locator('#search').fill('')
                page.evaluate('window.scrollTo(0,0);document.getElementById("toast").hidden=true')
                page.screenshot(path=str(ROOT/'docs/pagination-preview.png'), full_page=True)
                # Automatic school mode uses the actual ready/catalog fields and
                # never switches while a demo execution is active.
                page.locator('#mode').select_option('demo')
                ready=page.request.get(url+'api/state?mode=school').json()
                ready['session'].update(state='connected',selection_ready=True,browser_running=True)
                ready['catalog_sync'].update(state='success',message='本地fixture目录已读取')
                page.route('**/api/state?mode=*', lambda route: route.fulfill(json=ready))
                page.reload(wait_until='networkidle')
                expect(page.locator('#mode')).to_have_value('school')
                page.locator('#openConnection').click()
                expect(page.locator('#sessionTitle')).to_have_text('课程列表已打开')
                ready['run']['phase']='running'
                page.evaluate("localStorage.setItem('course-mode','demo')")
                page.reload(wait_until='networkidle')
                expect(page.locator('#mode')).to_have_value('demo')
                expect(page.locator('#mode')).to_be_disabled()
                page.unroute('**/api/state?mode=*')
                # Complete school columns and conflict pairs, using only synthetic data.
                page.reload(wait_until='networkidle')
                page.locator('#mode').select_option('school')
                page.locator('#openConnection').click()
                meetings=[{'weekday':day,'start':10,'end':11,'weeks':[7,8,9,10,11,12,13,14]} for day in (1,3)]
                courses=[]
                for index in (1,2):
                    courses.append({'id':f'full:{index}','name':f'完整测试课程{index}','class_code':f'TEST{index}.01',
                        'semester':'fixture-term','teachers':['测试教师'],'schedule_known':True,'meetings':meetings,
                        'school_display':{'verified':True,'operation':'选课','status':'待选课',
                            'info':f'完整测试课程{index}\nTEST{index}|测试学院|理论课\n2学分|32学时|公选|选修|中文授课|考试',
                            'module':'测试课程模块','lesson':f'TEST{index}.01\n测试专业限制','teachers':'测试教师',
                            'time_place':'测试校区\n7~14周 星期一 10~11节 测试楼101;\n7~14周 星期三 10~11节 测试楼102','enrollment':'10/30'}})
                existing={'id':'full:existing','name':'已有测试课程','class_code':'EXIST.01','semester':'fixture-term',
                    'schedule_known':True,'meetings':[{**m,'weeks':[7,8,9,10,11]} for m in meetings]}
                page.locator('#importFile').set_input_files({'name':'full-fixture.json','mimeType':'application/json',
                    'buffer':json.dumps({'catalog':courses,'existing':[existing],'coverage':'complete'},ensure_ascii=False).encode('utf-8')})
                expect(page.locator('#catalogCount')).to_have_text('2')
                page.locator('.nav-item[data-page="plan"]').click()
                page.locator('#pageSize').select_option('10')
                page.locator('[data-course="full:1"]').check()
                page.locator('[data-course="full:2"]').check()
                expect(page.locator('#chosenCount')).to_have_text('2')
                expect(page.locator('#conflictCount')).to_have_text('3')
                expect(page.locator('#conflictLabel')).to_have_text('计划之间 1 组 · 与已有课 2 组')
                expect(page.locator('#planIssues')).to_contain_text('已有测试课程')
                expect(page.locator('#planIssues')).to_contain_text('第 7–11 周')
                expect(page.locator('#catalogHeaders th')).to_have_text(['操作','选课状态','课程信息','所属课程模块','教学班','授课教师','时间地点','已选/人数上限','加入计划'])
                first=page.locator('#courseRows tr').first
                for text in ('32学时|公选|选修|中文授课|考试','测试课程模块','测试专业限制','星期三 10~11节 测试楼102'):
                    expect(first).to_contain_text(text)
                page.locator('#search').fill('测试课程模块')
                expect(page.locator('#courseRows tr')).to_have_count(2)
                page.locator('#search').fill('')
                page.set_viewport_size({'width':1920,'height':1080})
                page.evaluate('document.querySelector(".table-scroll").scrollLeft=0;document.querySelector(".table-scroll").scrollTop=0;document.getElementById("toast").hidden=true')
                page.screenshot(path=str(ROOT/'docs/school-columns-preview.png'),full_page=True)
                assert not errors, errors
                print(json.dumps({'browser_smoke':'passed','demo_successes':4,'school_submission_disabled':True,
                                  'retry_limit':10,'periods':12,'both_conflict_types':True,
                                  'mobile_width':dimensions['width'],'theme_persistence':True,'pagination_jump':True,
                                  'fluid_widths':[2560,1920,1280,960,390], 'auto_school_mode':True,'compact_shell':True,'three_navigation_entries':True,'small_signature':True,'javascript_errors':errors}, ensure_ascii=False))
                browser.close()
        finally:
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    main()
