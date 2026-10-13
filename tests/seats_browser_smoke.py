"""Real Chromium checks of monitoring controls and SSE, isolated demo only."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'CUPK_STATE_DIR':temp}
        binary=sys.executable
        if os.name=='nt' and sys.prefix!=sys.base_prefix:
            binary=sys._base_executable
            env['PYTHONPATH']=sysconfig.get_path('purelib')+os.pathsep+env.get('PYTHONPATH','')
        server=subprocess.Popen([binary,'-m','assistant','--port',str(port)],cwd=ROOT,
                                env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            url=f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    urllib.request.urlopen(url+'/api/health',timeout=.5).close();break
                except OSError:time.sleep(.1)
            errors=[]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1280,'height':900})
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url,wait_until='networkidle');page.keyboard.press('Escape')
                page.locator('#samplePlan').click()
                expect(page.locator('#chosenCount')).to_have_text('4')
                plan_before=page.request.get(url+'/api/state').json()['plan']
                page.locator('#openSeats').click()
                expect(page.locator('#seatModeLabel')).to_contain_text('模拟数据')
                page.locator('#seatUsePlan').click()
                assert len(page.locator('#seatCodes').input_value().splitlines())==4
                page.locator('#seatForm button[type="submit"]').click()
                expect(page.locator('.seat-target')).to_have_count(4)
                assert page.locator('#seatInterval').input_value()=='5'
                page.locator('#seatStart').click()
                expect(page.locator('#seatStatus')).to_contain_text('正在监测')
                expect(page.locator('.seat-target').first).to_contain_text('2 / 30')
                before=page.request.get(url+'/api/seats?mode=demo').json()['meta']['last_success']
                page.wait_for_function("async stamp => (await (await fetch('/api/seats?mode=demo')).json()).meta.last_success !== stamp",arg=before,timeout=9000)
                page.locator('#seatPause').click()
                expect(page.locator('#seatStatus')).to_contain_text('已暂停')
                page.locator('#seatStop').click()
                expect(page.locator('#seatStatus')).to_contain_text('已停止')
                assert page.request.get(url+'/api/state').json()['plan']==plan_before
                page.locator('#seatCatalogDetails summary').click()
                page.locator('#seatCatalogRefresh').click()
                expect(page.locator('.seat-catalog-rows>div')).to_have_count(9)
                expect(page.locator('#seatCatalogStatus')).to_contain_text('更新')
                page.locator('#seatDialog').evaluate('e=>e.scrollTop=0')
                page.screenshot(path=str(ROOT/'docs/seat-monitor-demo.png'))
                for width in (1280,960,390):
                    page.set_viewport_size({'width':width,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    rect=page.locator('#seatDialog').bounding_box()
                    assert rect['width']<=width+1
                page.keyboard.press('Escape')
                page.locator('#themeToggle').click();page.locator('#openSeats').click()
                expect(page.locator('.seat-target')).to_have_count(4)
                assert not errors,errors
                print(json.dumps({'seat_browser_smoke':'passed','sse_updates':True,'five_second_refresh':True,
                                  'pause_stop':True,'plan_preserved':True,'catalog_batch_demo':9,
                                  'widths':[1280,960,390],'javascript_errors':errors}))
                browser.close()
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__=='__main__':main()
