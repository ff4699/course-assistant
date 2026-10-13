"""Evidence-backed guide and elapsed-time summaries for the local workflow."""
from datetime import datetime, timezone


def run_summary(run):
    targets=run.get('targets',[])
    successful=sum(t['status']=='succeeded' for t in targets)
    elapsed=run.get('elapsed_ms')
    if run.get('phase') in {'running','stopping'} and run.get('segment_started_at'):
        elapsed=(elapsed or 0)+max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(run['segment_started_at'])).total_seconds()*1000)
    finished=run.get('phase') in {'finished','stopped','interrupted'}
    if not targets:feedback='保存计划后开始执行'
    elif not finished:feedback=f'已确认 {successful}/{len(targets)} 门，正在处理'
    elif successful==len(targets):
        if not any(t.get('attempts',0) for t in targets):feedback='目标课程此前已选，已核验，无需重复提交'
        elif (elapsed or 0)<=15000:feedback='全部目标课程已核验成功，选课顺利完成！'
        else:feedback='全部目标课程已核验成功，等待结束，课程已安排好。'
    else:feedback=f'已确认 {successful}/{len(targets)} 门；未完成课程和原因已保留，可检查后继续。'
    return {'elapsed_ms':round(elapsed or 0,1),'finished':finished,'successful':successful,
            'unsuccessful':len(targets)-successful,'feedback':feedback,
            'timetable_updated':run.get('timetable_updated',False)}


def guide(mode,session,catalog,existing,plan,analysis,coverage,sync,catalog_sync,run,live_submit=False):
    demo=mode=='demo'
    fresh=sync.get('state') not in {'error','waiting_login','partial','interrupted','awaiting_semester'}
    table_ready=coverage=='complete' and fresh
    catalog_ready=bool(catalog) and (demo or catalog_sync.get('coverage')=='complete' and catalog_sync.get('state')=='success' and catalog_sync.get('context_verified'))
    plan_ready=bool(plan.course_ids) and not any(i['kind']=='missing' for i in analysis['issues'])
    semester_ready=demo or bool(catalog and existing) and all(c.semester not in {'','unverified'} for c in catalog+existing) and len({c.semester for c in catalog+existing})==1
    conflicts_ready=plan_ready and table_ready and semester_ready and analysis['can_run']
    summary=run_summary(run)
    running=run.get('phase') in {'running','waiting','stopping'}
    targets=run.get('targets',[])
    execution_done=bool(targets) and summary['finished']
    results_done=execution_done and summary['successful']==len(targets) and summary['timetable_updated']
    steps=[
        ('login','登录教务','done' if not demo and session['state']=='connected' else 'not_required' if demo else 'pending',
         '演练无需学校登录' if demo else session['message'],'connection'),
        ('timetable','同步已有课表','done' if table_ready else 'running' if sync.get('state')=='running' else 'attention' if sync.get('state') in {'error','waiting_login','partial'} else 'pending',
         f'{len(existing)}门，完整列表已核实' if table_ready else sync.get('message','等待同步'),'connection'),
        ('catalog','读取课程目录','done' if catalog_ready else 'running' if catalog_sync.get('state')=='running' else 'attention' if catalog else 'pending',
         f'{len(catalog)}个教学班' if catalog_ready else catalog_sync.get('message','等待读取'),'connection'),
        ('plan','保存预选计划','done' if plan_ready else 'pending',f'{len(plan.course_ids)}/5门','plan'),
        ('conflicts','检查时间冲突','done' if conflicts_ready else 'attention' if plan_ready else 'pending',
         '已有课表与预选之间、预选之间已检查' if conflicts_ready else '需核实同一学期、全部时间及两类冲突','plan'),
        ('execute','执行选课','running' if running else 'done' if execution_done else 'pending' if demo or live_submit else 'unavailable',
         '每门课依次提交' if demo or live_submit else '真实提交接口与结果核验仍待验证','plan'),
        ('results','核验并更新课表','done' if results_done else 'attention' if execution_done else 'pending',
         summary['feedback'] if targets else '确认教学班成功后更新课表','monitor')]
    return [{'id':i,'label':l,'state':s,'message':m,'page':p} for i,l,s,m,p in steps]
