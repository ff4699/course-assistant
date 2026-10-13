'use strict';
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dayNames = ['一','二','三','四','五','六','日'];
const names = {plan:['选课计划','选课计划','先同步已有课表，再比较上课时间与选择顺序。'],schedule:['我的课表','我的课表','已有课程与预选课程一起显示，按周次、节次和具体时刻检查冲突。'],monitor:['执行结果','执行结果','每门课程的结果、用时与失败原因。']};
const statusNames = {pending:'等待执行',submitting:'正在提交',unconfirmed:'结果待确认',succeeded:'已核验成功',full:'人数已满',blocked:'无法选择',exhausted:'重试已用完',cooldown:'等待重试',paused:'尚未完成'};
const phaseNames = {idle:'尚未执行',waiting:'等待开始',running:'正在演练',stopping:'停止中',stopped:'已停止',finished:'演练结束',interrupted:'执行中断'};
let mode = localStorage.getItem('cupk-mode') === 'school' ? 'school' : 'demo';
let page = 'plan', state = null, draft = null, saveQueue = Promise.resolve(), pendingSaves = 0, toastTimer;
let pendingOperations=0, polling=false, viewerBusy=false, viewerUrl=null, lastServerContact=Date.now();
let lastCatalogSignature='';
let autoSchoolShown=false;
let guideAutoShown=localStorage.getItem('cupk-guide-seen')==='1',alertDestination='conflictDialog';
let catalogPage=1, catalogPageSize=10, catalogPageCount=1;
const preferredPageSize=localStorage.getItem('cupk-page-size');
if(['auto','10','20','50'].includes(preferredPageSize))$('pageSize').value=preferredPageSize;
if($('pageSize').value!=='auto')catalogPageSize=Number($('pageSize').value);
function renderTheme(){
  const dark=document.documentElement.dataset.theme==='dark';
  $('themeToggle').textContent=dark?'☾':'☀';
  $('themeToggle').setAttribute('aria-pressed',String(dark));
  $('themeToggle').title=dark?'黑色背景 · 点击切换白色背景':'白色背景 · 点击切换黑色背景';
}
$('themeToggle').onclick=()=>{
  document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';
  localStorage.setItem('cupk-theme',document.documentElement.dataset.theme);renderTheme();
};
renderTheme();
$('aboutButton').onclick=()=>$('aboutDialog').showModal();
$('aboutClose').onclick=()=>$('aboutDialog').close();
function closeSideDialogs(){document.querySelectorAll('.side-panel[open]').forEach(dialog=>dialog.close());}
function openDialog(id){closeSideDialogs();const dialog=$(id);if(dialog&&!dialog.open)dialog.showModal();}
document.querySelectorAll('[data-close-dialog]').forEach(button=>button.onclick=()=>$(button.dataset.closeDialog).close());
document.querySelectorAll('.side-panel').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}));
$('openConnection').onclick=()=>openDialog('connectionDialog');
$('openGuide').onclick=()=>{localStorage.setItem('cupk-guide-seen','1');openDialog('guideDialog');};
$('openPlan').onclick=()=>openDialog('planDialog');
$('openConflicts').onclick=()=>openDialog('conflictDialog');
$('alertDetails').onclick=()=>openDialog(alertDestination);

async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{'Content-Type':'application/json','X-CUPK-Client':'local',...(options.headers || {})}});
  let result;
  try { result = await response.json(); } catch { throw new Error('服务暂时不可用，请检查本地程序是否运行'); }
  if (!response.ok) throw new Error(result.detail || '操作没有完成');
  return result;
}
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{$('toast').hidden=true;},5000); }
const active = () => state && ['waiting','running','stopping'].includes(state.run.phase);
const byId = (id) => state?.catalog.find(c => c.id===id);
const anyCourse = (id) => byId(id)||state?.existing.find(c=>c.id===id);
function conflictSummary(){
  const pairs=state.analysis.issues.filter(i=>i.kind==='conflict');
  return state.analysis.conflict_summary||{pairs:pairs.length,plan_pairs:pairs.filter(i=>i.scope==='plan').length,existing_pairs:pairs.filter(i=>i.scope==='existing').length};
}
function weeksText(weeks) {
  if (!weeks.length) return '周次待确认';
  const sorted=[...new Set(weeks)].sort((a,b)=>a-b);
  if (sorted.length>1 && sorted.every((w,i)=>!i || w===sorted[i-1]+1)) return `第 ${sorted[0]}–${sorted.at(-1)} 周`;
  return `第 ${sorted.join('、')} 周`;
}
function timeText(course) {
  if (!course.schedule_known) return '上课时间待核实';
  if (!course.meetings.length) return '无固定排课';
  return course.meetings.map(slot=>`周${dayNames[slot.weekday-1]} · ${window.CupkTimetable.range(slot,state.calendar)} · ${weeksText(slot.weeks)}`).join('；');
}
async function loadState() {
  const requested=mode;
  const result=await api(`/api/state?mode=${requested}`);
  if (requested!==mode) return;
  state=result;
  if(!autoSchoolShown && mode==='demo' && !active() && !pendingSaves &&
      result.session.selection_ready && result.catalog_sync?.state==='success'){
    autoSchoolShown=true;
    await switchMode('school');
    return;
  }
  lastServerContact=Date.now();$('serviceWarning').hidden=true;
  if (!pendingSaves) draft=structuredClone(result.plan);
  render();
  if(!guideAutoShown&&!active()){
    guideAutoShown=true;localStorage.setItem('cupk-guide-seen','1');openDialog('guideDialog');
  }
}
function savePlan() {
  const payload=structuredClone(draft), destination=mode;
  pendingSaves++;
  render();
  const action=saveQueue.catch(()=>{}).then(()=>api(`/api/plan?mode=${destination}`,{method:'PUT',body:JSON.stringify(payload)}));
  saveQueue=action.finally(async()=>{pendingSaves--; await loadState();});
  saveQueue.catch(error=>toast(error.message));
  return saveQueue;
}
function navigate(next) {
  if(next==='connection'){openDialog('connectionDialog');return;}
  if(next==='logs')next='monitor';
  closeSideDialogs();
  page=next;
  document.body.dataset.page=next;
  window.scrollTo(0,0);
  document.querySelectorAll('.nav-item').forEach(e=>e.classList.toggle('active',e.dataset.page===page));
  document.querySelectorAll('.page-section').forEach(e=>{e.hidden=e.id!==`page-${page}`;});
  $('breadcrumb').textContent=names[page][0]; $('pageTitle').textContent=names[page][1]; $('pageSubtitle').textContent=names[page][2];
  $('settingsButton').hidden=page!=='plan';
  if (state) render();
}
function render() {
  if (!state || !draft) return;
  const school=mode==='school';
  $('mode').value=mode; $('mode').disabled=active();
  $('environmentBadge').textContent=school?'学校模式 · 预选':'模拟环境';
  $('catalogCount').textContent=state.catalog.length; $('catalogBadge').textContent=state.catalog.length;
  $('chosenCount').textContent=draft.course_ids.length; $('navCount').textContent=draft.course_ids.length; $('planBadge').textContent=`${draft.course_ids.length} / 5`;
  $('creditsLabel').textContent=`${draft.course_ids.reduce((n,id)=>n+(byId(id)?.credits||0),0)} 学分 · 可调整优先级`;
  const conflicts=conflictSummary(),conflictCount=conflicts.pairs;
  $('conflictCount').textContent=school&&state.coverage!=='complete'?'—':conflictCount;
  $('conflictLabel').textContent=school&&state.coverage!=='complete'?'已有课程尚未完整核实':conflictCount?`计划之间 ${conflicts.plan_pairs} 组 · 与已有课 ${conflicts.existing_pairs} 组`:'与已有课程一起检查';
  $('semesterLabel').textContent=school?'已导入的学校课程目录':'2026–2027 · 秋季演练';
  $('noticeTitle').textContent=school?'学校模式：先完成登录与数据核验':'先在这里演练一次';
  $('noticeCopy').textContent=school?'第一版支持预选与冲突检查；真实选课提交待本校适配验证。':'以下课程和名额为模拟数据，操作不会影响学校课程。';
  $('samplePlan').hidden=school;
  $('tableFootnote').textContent=school?`${state.catalog_sync?.message||'课程目录完整性待核实'}${state.catalog_sync?.last_success?' · 上次读取 '+new Date(state.catalog_sync.last_success).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):''}`:'演练数据 · 不代表学校实时名额';
  $('refreshCatalog').textContent=school?'↻ 读取页面':'↻ 刷新';
  $('readAllCatalog').hidden=!school;
  $('readAllCatalog').disabled=state.catalog_sync?.state==='running';
  renderChosen(); renderRun(); renderSchedule(); renderConnection(); renderLogs(); renderWorkflow(); renderCatalog();
  $('sessionDot').className=state.session.state==='connected'?'status-dot':'';
  renderShell();
}
function renderShell(){
  if(!state||!draft)return;
  const continuation=mode==='demo'&&state.run.summary?.finished&&state.run.targets.some(t=>t.status!=='succeeded');
  $('planBar').hidden=page!=='plan'&&!active()&&!(page==='monitor'&&continuation);
  if(page==='monitor'&&continuation&&!active())$('startRun').hidden=true;
  const chips=draft.course_ids.map(id=>{const c=byId(id);return c?`<button class="plan-chip" data-plan-chip title="${esc(timeText(c))}">${esc(c.name)} · ${esc(c.class_code||c.code)}</button>`:'';}).join('')||'<span class="quiet">从目录勾选目标教学班</span>';
  if($('planChips').innerHTML!==chips)$('planChips').innerHTML=chips;
  document.querySelectorAll('[data-plan-chip]').forEach(button=>button.onclick=()=>openDialog('planDialog'));
  const ready=(state.guide||[]).filter(step=>['done','not_required'].includes(step.state)).length;
  $('guideSummary').textContent=`准备 ${ready}/${(state.guide||[]).length||7}`;
  $('connectionLabel').textContent=mode==='demo'?'教务连接':state.session.state==='connected'?'教务已连接':state.session.state==='manual_required'?'需要验证':'连接教务';
  $('openConnection').title=state.session.message;
  const pairs=conflictSummary(),unknownExisting=state.existing.filter(c=>!c.schedule_known).length;
  const unknownPlanned=draft.course_ids.filter(id=>byId(id)&&!byId(id).schedule_known).length;
  $('openConflicts').classList.toggle('has-issues',state.analysis.issues.some(i=>i.kind!=='full'));
  let message='',destination='conflictDialog',attention=false;
  if(mode==='demo')message='模拟演练 · 示例课程与名额，不影响学校选课';
  else if(state.session.state==='manual_required'){message='学校会话需要正常验证，请在学校窗口完成登录';destination='connectionDialog';attention=true;}
  else if(state.analysis.issues.some(i=>['coverage','freshness','semester'].includes(i.kind))){message='已有课表或学期信息待核实，请先同步正确课表';destination='connectionDialog';attention=true;}
  else if(unknownExisting||unknownPlanned){message=`${unknownExisting?`已有课表 ${unknownExisting} 门时间待核实`:''}${unknownPlanned?` · 计划 ${unknownPlanned} 门时间待核实`:''}`;attention=true;}
  else if(pairs.pairs){message=`时间冲突：计划之间 ${pairs.plan_pairs} 组，与已有课 ${pairs.existing_pairs} 组`;attention=true;}
  else if(!state.catalog.length){message='连接教务后同步课程，或导入课程文件';destination='connectionDialog';}
  else if(['error','waiting_page','waiting_login','interrupted'].includes(state.catalog_sync?.state)){message=state.catalog_sync.message;destination='connectionDialog';attention=true;}
  if(unknownExisting||unknownPlanned)if(pairs.pairs)message+=` · 计划之间 ${pairs.plan_pairs} 组，与已有课 ${pairs.existing_pairs} 组`;
  $('compactAlert').hidden=!message;$('compactAlert').classList.toggle('attention',attention);$('compactAlertText').textContent=message;
  $('alertDetails').textContent=attention?'查看问题 →':'准备指引 →';
  alertDestination=mode==='demo'?'guideDialog':destination;
  if(mode==='demo'&&state.analysis.issues.some(i=>i.kind!=='full')){message+=` · 当前有 ${pairs.pairs} 组冲突或待核实信息`;$('compactAlertText').textContent=message;$('alertDetails').textContent='查看问题 →';alertDestination='conflictDialog';$('compactAlert').classList.add('attention');}
  $('resultsEmpty').hidden=!!state.run.targets.length;
}
function renderCatalog() {
  if(!state||!draft)return;
  const school=mode==='school';
  const table=document.querySelector('.course-table');
  table.classList.toggle('school-catalog',school);
  const columns=school?['操作','选课状态','课程信息','所属课程模块','教学班','授课教师','时间地点','已选/人数上限','加入计划']:['课程 / 教学班','授课教师','上课时间','已选 / 上限','剩余','预选'];
  const headers=columns.map(c=>`<th>${esc(c)}</th>`).join('');
  if($('catalogHeaders').innerHTML!==headers)$('catalogHeaders').innerHTML=headers;
  if($('pageSize').value==='auto'){
    const available=document.querySelector('.catalog-panel .table-scroll').clientHeight;
    const nextSize=page==='plan' && available>0 ?
      Math.max(1,Math.min(30,Math.floor((available-38)/(school?150:68)))) : 10;
    if(nextSize!==catalogPageSize){
      const first=(catalogPage-1)*catalogPageSize;
      catalogPageSize=nextSize;catalogPage=Math.floor(first/nextSize)+1;
    }
  }
  const query=$('search').value.trim().toLowerCase(), filter=$('filter').value;
  const signature=JSON.stringify([mode,state.catalog,draft.course_ids,active(),query,filter,innerWidth<=650,catalogPage,catalogPageSize]);
  if(signature===lastCatalogSignature)return;
  lastCatalogSignature=signature;
  const list=state.catalog.filter(c=>(`${c.name} ${c.code} ${c.class_code} ${c.teachers.join(' ')} ${Object.values(c.school_display||{}).join(' ')}`.toLowerCase().includes(query))&&(filter!=='available'||c.remaining===null||c.remaining>0)&&(filter!=='chosen'||draft.course_ids.includes(c.id)));
  catalogPageCount=Math.max(1,Math.ceil(list.length/catalogPageSize));
  catalogPage=Math.min(catalogPageCount,Math.max(1,catalogPage));
  const offset=(catalogPage-1)*catalogPageSize;
  const pageList=list.slice(offset,offset+catalogPageSize);
  $('catalogPrev').disabled=catalogPage===1;$('catalogNext').disabled=catalogPage===catalogPageCount;
  $('catalogPageLabel').textContent=`${catalogPage} / ${catalogPageCount}`;
  $('pageJump').max=catalogPageCount;if(document.activeElement!==$('pageJump'))$('pageJump').value=catalogPage;
  $('pageRange').textContent=`已保存目录 · ${list.length?offset+1:0}–${Math.min(list.length,offset+catalogPageSize)} / ${list.length} 个教学班`;
  document.querySelector('.catalog-panel').style.setProperty('--visible-rows',Math.max(1,pageList.length));
  $('courseRows').innerHTML=pageList.map(course=>{
    const chosen=draft.course_ids.includes(course.id), remaining=course.remaining;
    if(school){
      const d=course.school_display||{};
      const info=d.info||`${course.name}\n${course.code||'课程代码待核实'} · ${course.credits} 学分`;
      const cells=[d.operation||'—',d.status||'尚未读取',info,d.module||'—',d.lesson||course.class_code||'教学班待核实',d.teachers||course.teachers.join(' / ')||'教师待核实',d.time_place||timeText(course)];
      return `<tr class="${chosen?'chosen':''}" data-display-complete="${!!d.verified}">${cells.map((v,i)=>`<td class="school-cell school-cell-${i}">${esc(v)}</td>`).join('')}<td>${esc(d.enrollment||`${course.enrolled_count??'—'}/${course.capacity??'—'}`)}<small class="remaining-note">剩余 ${remaining??'未知'}</small></td><td><input type="checkbox" data-course="${esc(course.id)}" ${chosen?'checked':''} ${active()?'disabled':''} aria-label="预选${esc(course.name)} ${esc(course.class_code)}"></td></tr>`;
    }
    return `<tr class="${chosen?'chosen':''}"><td><div class="course-title">${esc(course.name)}</div><div class="course-meta"><span>${esc(course.class_code||course.code||'班级待核实')}</span><span>${course.credits} 学分</span></div></td><td>${esc(course.teachers.join(' / ')||'待核实')}</td><td class="course-time">${esc(timeText(course))}<br><small>${esc(course.meetings.length?weeksText(course.meetings[0].weeks)+(course.meetings.length>1?` · 另有 ${course.meetings.length-1} 个时段`:''):'')}</small></td><td>${course.enrolled_count??'—'} <span style="color:#c1c9d4">/</span> ${course.capacity??'—'}</td><td><span class="seat ${remaining===0?'full':remaining!==null&&remaining<=3?'low':''}">${remaining??'—'}</span></td><td><input type="checkbox" data-course="${esc(course.id)}" ${chosen?'checked':''} ${active()?'disabled':''} aria-label="预选${esc(course.name)} ${esc(course.class_code)}"></td></tr>`;
  }).join('');
  $('catalogEmpty').hidden=list.length>0;
  $('catalogEmpty').textContent=state.catalog.length?'没有匹配的课程，试试课程代码或老师姓名。':mode==='school'?'还没有学校课程。到「连接教务」读取页面或导入课程文件。':'目录暂时为空。';
  $('tableSummary').textContent=school?'学校原信息完整换行显示 · 横向滚动查看全部列 →':innerWidth<=650?'← 横向滑动，查看时间、名额和预选按钮 →':`显示 ${list.length} 个教学班 · 课程按班级分别识别`;
  document.querySelectorAll('[data-course]').forEach(input=>input.onchange=()=>{
    const id=input.dataset.course;
    if(input.checked){if(draft.course_ids.length>=5){input.checked=false;toast('第一版每个计划最多预选 5 门课');return;}draft.course_ids.push(id);}
    else draft.course_ids=draft.course_ids.filter(v=>v!==id);
    savePlan();
  });
}
function renderChosen() {
  $('chosenCourses').style.setProperty('--chosen-columns',Math.max(1,draft.course_ids.filter(id=>byId(id)).length));
  $('chosenCourses').innerHTML=draft.course_ids.length?draft.course_ids.map((id,i)=>{
    const c=byId(id); if(!c) return '';
    return `<div class="chosen-card"><span class="priority-number">${i+1}</span><div class="chosen-info"><strong>${esc(c.name)}</strong><small>${esc(c.teachers.join(' / '))} · ${esc(timeText(c))}</small><small>${c.remaining===null?'名额待核实':`剩余 ${c.remaining} 个名额`}</small></div><div class="order-controls"><button class="icon-button" data-move="${i}" data-direction="-1" ${i===0||active()?'disabled':''} aria-label="提高${esc(c.name)}优先级">⌃</button><button class="icon-button" data-move="${i}" data-direction="1" ${i===draft.course_ids.length-1||active()?'disabled':''} aria-label="降低${esc(c.name)}优先级">⌄</button><button class="icon-button" data-remove="${i}" ${active()?'disabled':''} aria-label="移除${esc(c.name)}">×</button></div></div>`;
  }).join(''):'<div class="chosen-empty"><span>◫</span><small>从下方课程目录勾选目标教学班，保存你的计划。</small></div>';
  document.querySelectorAll('[data-move]').forEach(button=>button.onclick=()=>{
    const index=Number(button.dataset.move), other=index+Number(button.dataset.direction);
    [draft.course_ids[index],draft.course_ids[other]]=[draft.course_ids[other],draft.course_ids[index]]; savePlan();
  });
  document.querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>{draft.course_ids.splice(Number(button.dataset.remove),1);savePlan();});
  const pairs=conflictSummary();
  $('planIssues').innerHTML=(pairs.pairs?`<div class="conflict-explanation">已保存 ${draft.course_ids.length} 门计划；计划之间 ${pairs.plan_pairs} 组、与已有课 ${pairs.existing_pairs} 组。数字按课程对计数。</div>`:'')+state.analysis.issues.map(issue=>{
    const course=anyCourse(issue.course_id),other=anyCourse(issue.other_id);
    const title=issue.kind==='conflict'?`[${issue.scope==='existing'?'计划与已有课':'计划之间'}] ${course?.name||issue.course_id}（${course?.class_code||'班级待核实'}） ↔ ${other?.name||issue.other_id}（${other?.class_code||'班级待核实'}）`:`${course?.name||'课表'}：${issue.message}`;
    return `<div class="issue ${['full','coverage','freshness'].includes(issue.kind)?'warning':''}"><strong>${esc(title)}</strong>${issue.overlap?'<br>'+issue.overlap.map(slot=>esc(`周${dayNames[slot.weekday-1]} ${window.CupkTimetable.range(slot,state.calendar)} · ${weeksText(slot.weeks)}`)).join('<br>'):''}</div>`;
  }).join('');
  $('strategyLabel').textContent=draft.scarcity_first?'优先照顾名额较少的课程':'严格按个人优先级执行';
  $('retryLabel').textContent=`失败重试 ${draft.retries} 次 · 最多提交 ${draft.retries+1} 次`;
  $('startRun').hidden=active(); $('stopRun').hidden=!active();
  $('startRun').disabled=mode==='school'||!state.analysis.can_run||pendingSaves>0;
  $('startRun').innerHTML=mode==='school'?'真实选课暂未启用':'开始模拟演练';
  $('runDisclaimer').textContent=mode==='school'?'学校计划可以保存，第一版不提交真实选课。':'每门课顺序提交，再统一核验结果。';
  updateCountdown();
}
function updateCountdown(){
  const start=state?.run.start_at;
  if(start&&state.run.phase==='waiting'){
    const seconds=Math.max(0,Math.ceil((new Date(start)-Date.now())/1000));
    $('countdown').textContent=`距离演练开始 ${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
  }else $('countdown').textContent=draft?.start_at?`定时：${new Date(draft.start_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间）`:'';
}
function renderRun(){
  $('resultPanel').hidden=!state.run.targets.length;
  $('runPhase').textContent=phaseNames[state.run.phase]||state.run.phase;
  const summary=state.run.summary;
  $('runSummary').textContent=summary&&state.run.targets.length?`总执行用时 ${(summary.elapsed_ms/1000).toFixed(2)} 秒 · 成功 ${summary.successful} 门 · 未完成 ${summary.unsuccessful} 门。${summary.feedback}${summary.timetable_updated?' · 课表已更新':summary.finished&&summary.successful?' · 课表更新待重试':''}`:'';
  $('runTargets').innerHTML=state.run.targets.map(t=>`<div class="run-card"><strong>${esc(byId(t.id)?.name||t.id)}</strong><br><span class="tag ${esc(t.status)}">${esc(statusNames[t.status]||t.status)}</span><p>${esc(t.reason)}</p><p>提交 ${t.attempts} 次${t.evidence?` · ${esc(t.evidence)}核验`:''}</p><p>${t.submission_ms!=null?`响应 ${t.submission_ms} 毫秒`:t.attempts?'响应尚未确认':'尚无提交'}${t.verification_ms!=null?` · 核验 ${t.verification_ms} 毫秒`:''}</p></div>`).join('');
  $('resumeRun').hidden=mode!=='demo'||active()||!summary?.finished||!state.run.targets.some(t=>t.status!=='succeeded');
}
function renderSchedule(){
  const outdated=mode==='school'&&['error','partial','waiting_login','interrupted','awaiting_semester'].includes(state.sync.state);
  $('coverageNotice').innerHTML=outdated?`<div class="coverage-note">${esc(state.sync.message)}。当前仍显示上次保存的课表，不代表已成功更新。</div>`:state.coverage==='complete'?'':`<div class="coverage-note">${state.coverage==='scheduled_only'?'已显示读取到的已有课程，但完整列表尚未核实；不能认定没有其他课程冲突。':'请先导入本学期完整已有课表，再检查预选课程。'}</div>`;
  $('timetableSyncMessage').textContent=mode==='demo'?'演练已有课程已加载，时间轴采用你提供的校历。':`${state.sync.message}${state.sync.last_success?' · 上次成功 '+new Date(state.sync.last_success).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):''}`;
  $('syncTimetable').disabled=mode==='demo'||state.sync.state==='running';
  $('autoSync').disabled=mode==='demo'||!state.sync.config.semester;
  $('autoSync').checked=mode==='school'&&state.sync.config.enabled;
  $('autoSync').title=mode==='demo'?'演练数据不访问学校':!state.sync.config.semester?'先成功导入学校当前学期课表':'每 60 秒读取一次学校课表';
  window.CupkTimetable.render(state,draft);
}
function renderConnection(){
  const labels={not_started:'尚未连接',checking:'检查登录状态',manual_required:'等待人工验证',connected:'学生教务已连接',uncertain:'需要检查连接'};
  $('sessionTitle').textContent=state.session.state==='connected'?(state.session.selection_ready?'课程列表已打开':'已登录，等待进入课程列表'):(labels[state.session.state]||state.session.state);
  $('sessionMessage').textContent=`${state.session.message}。目录：${state.catalog_sync?.message||'等待读取'}${state.catalog_sync?.last_success?' · 上次成功 '+new Date(state.catalog_sync.last_success).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):''}`;
  const wasVisible=!$('loginArea').hidden;
  $('loginArea').hidden=state.session.state!=='manual_required';
  if(!wasVisible&&!$('loginArea').hidden)$('captchaImage').src=`/api/browser/captcha?t=${Date.now()}`;
}
function renderLogs(){
  $('resetDemo').disabled=mode!=='demo'||active();
  $('eventList').innerHTML=state.run.events.length?state.run.events.slice().reverse().map(event=>`<div class="event ${esc(event.kind)}"><time>${new Date(event.time).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</time><span>${esc(event.message)}</span></div>`).join(''):'<div class="empty">还没有执行记录。选好课程后，先完成一次模拟演练。</div>';
}
async function busy(button, action){
  const text=button.textContent;button.disabled=true;button.textContent='处理中…';pendingOperations++;
  try{await action();}catch(error){toast(error.message);try{await loadState();}catch{showServiceError();}}finally{pendingOperations--;button.disabled=false;button.textContent=text;}
}

function showServiceError(){ $('serviceWarning').hidden=false;$('serviceWarning').textContent='本地服务连接中断，当前结果未确认。请检查程序窗口是否仍在运行，再刷新状态。'; }
function renderWorkflow(){
  if(!state||!draft)return;
  const icons={done:'✓',not_required:'—',running:'◌',attention:'!',pending:'○',unavailable:'○'};
  const guideHtml=(state.guide||[]).map((step,index)=>`<li><button class="guide-step ${esc(step.state)}" data-guide-page="${esc(step.page)}" title="${esc(step.message)}"><span>${icons[step.state]||'○'}</span><b>${index+1}. ${esc(step.label)}</b><small>${esc(step.message)}</small></button></li>`).join('');
  if($('setupGuide').innerHTML!==guideHtml)$('setupGuide').innerHTML=guideHtml;
  document.querySelectorAll('[data-guide-page]').forEach(button=>button.onclick=()=>navigate(button.dataset.guidePage));
  const activities=[state.session.activity,state.sync.activity].filter(job=>job.operation);
  const recent=activities.slice().sort((a,b)=>(b.updated_at||'').localeCompare(a.updated_at||''))[0];
  const running=activities.find(job=>job.state==='running');
  const run=state.run, succeeded=run.targets.filter(t=>t.status==='succeeded').length;
  let title='准备选课计划',message=state.coverage==='complete'?`已加载 ${state.existing.length} 门已有课程；${draft.course_ids.length?'正在检查预选课程冲突':'请选择要预选的课程'}`:'请先导入本学期完整已有课表',light='',start=null,end=null,waitingUser=false;
  if(active()){title='模拟选课执行中';message=run.progress?.message||phaseNames[run.phase];light='running';start=run.started_at;}
  else if(running||recent&&recent.updated_at>(run.progress?.updated_at||'')){
    const job=running||recent;title=job.operation;message=job.message;light=job.state==='running'?'running':job.state==='succeeded'?'success':job.state==='waiting_user'?'warning':'error';start=job.started_at;end=job.updated_at;waitingUser=job.state==='waiting_user';
  }else if(run.targets.length){title=`本次演练：已核验成功 ${succeeded}/${run.targets.length} 门`;message=run.progress?.message||'查看每门课的结果和未完成原因';light=succeeded===run.targets.length?'success':'warning';start=run.started_at;end=run.progress?.updated_at;}
  $('workflowTitle').textContent=title;$('workflowMessage').textContent=message;$('workflowLight').className='workflow-light '+light;
  $('workflowElapsed').textContent=start?`${waitingUser?'等待人工操作':light==='running'?'已耗时':'任务耗时'} ${Math.max(0,Math.floor(((light==='running'||waitingUser?Date.now():new Date(end||Date.now()).getTime())-new Date(start))/1000))} 秒`:'';
  const ready=state.coverage==='complete'&&!(mode==='school'&&['error','partial','waiting_login','interrupted','awaiting_semester'].includes(state.sync.state));
  const done=run.targets.length&&['finished','stopped','interrupted'].includes(run.phase);
  const conflictText=state.analysis.issues.some(i=>['coverage','freshness','unknown','semester'].includes(i.kind))?'数据待核实':state.analysis.issues.filter(i=>i.kind==='conflict').length+' 处冲突';
  const steps=[['导入已有课表',ready?'done':'',ready?`${state.existing.length} 门`:'待同步'],['检查两类冲突',state.analysis.can_run?'done':draft.course_ids.length?'error':'',conflictText],['顺序提交',active()?'current':done?'done':'',active()?'执行中':done?'已结束':'待执行'],['页面 / 课表核验',done?(succeeded===run.targets.length?'done':'error'):'',`${succeeded}/${run.targets.length||draft.course_ids.length} 门成功`]];
  $('workflowSteps').innerHTML=steps.map((s,i)=>`<div class="workflow-step ${s[1]}"><b>${i+1}</b><span>${esc(s[0])} · ${esc(s[2])}</span></div>`).join('');
  $('activityDetails').innerHTML=activities.length?activities.map(job=>`<h3>${esc(job.operation)} · ${esc({running:'执行中',succeeded:'已完成',failed:'未完成',waiting_user:'等待人工操作'}[job.state]||job.state)}</h3>${job.steps.map(step=>`<div class="activity-entry ${esc(step.state)}"><span>${new Date(step.time).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</span><strong>${esc(step.message)}</strong></div>`).join('')}<p class="quiet">${esc(job.message)}</p>`).join(''):'<div class="empty">连接学校或同步课表时，实际处理步骤会显示在这里。</div>';
  if(run.events.length)$('activityDetails').innerHTML+=`<h3>本次演练 · 最近步骤</h3>${run.events.slice(-8).map(event=>`<div class="activity-entry ${event.kind==='warning'?'waiting_user':''}"><span>${new Date(event.time).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</span><strong>${esc(event.message)}</strong></div>`).join('')}`;
  renderShell();
}
async function refreshView(){
  if(viewerBusy)return;
  if(!state?.session.browser_running){$('viewerStatus').textContent='尚未打开学校浏览器，请先连接教务。';return;}
  viewerBusy=true;
  try{
    const response=await fetch(`/api/browser/view?kind=${$('viewerKind').value}&t=${Date.now()}`);
    if(!response.ok){const result=await response.json();throw new Error(result.detail||'学校窗口暂时不能显示');}
    const url=URL.createObjectURL(await response.blob()),old=viewerUrl;viewerUrl=url;
    $('schoolView').src=url;$('schoolView').hidden=false;$('viewerEmpty').hidden=true;
    if(old)URL.revokeObjectURL(old);
    $('viewerStatus').textContent=`画面更新于 ${new Date().toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}（北京时间）`;
  }catch(error){$('viewerStatus').textContent=error.message;}finally{viewerBusy=false;}
}
async function switchMode(next){
  if(active()){toast('请先停止当前演练，再切换环境');$('mode').value=mode;return;}
  await saveQueue.catch(()=>{}); mode=next;catalogPage=1;localStorage.setItem('cupk-mode',mode); await loadState();window.dispatchEvent(new Event('cupkmodechange'));
}
document.querySelectorAll('.nav-item').forEach(button=>button.onclick=()=>navigate(button.dataset.page));
$('search').oninput=()=>{catalogPage=1;if(state)renderCatalog();}; $('filter').onchange=()=>{catalogPage=1;if(state)renderCatalog();};
$('catalogPrev').onclick=()=>{catalogPage--;renderCatalog();};
$('catalogNext').onclick=()=>{catalogPage++;renderCatalog();};
$('pageSize').onchange=()=>{localStorage.setItem('cupk-page-size',$('pageSize').value);if($('pageSize').value!=='auto')catalogPageSize=Number($('pageSize').value);catalogPage=1;renderCatalog();};
$('pageJumpForm').onsubmit=event=>{event.preventDefault();const target=Number($('pageJump').value);if(!Number.isInteger(target)||target<1||target>catalogPageCount){toast(`页码范围为 1–${catalogPageCount}`);return;}catalogPage=target;renderCatalog();};
$('readAllCatalog').onclick=()=>busy($('readAllCatalog'),async()=>{const r=await api('/api/browser/catalog/all',{method:'POST'});await loadState();toast(r.catalog_sync.message);});
$('openMonitor').onclick=()=>navigate('monitor');
$('openSchoolTimetable').onclick=()=>{$('viewerKind').value='timetable';navigate('monitor');$('browserViewDetails').open=true;refreshView();};
$('browserViewDetails').ontoggle=()=>{if($('browserViewDetails').open)refreshView();};
$('refreshView').onclick=refreshView;$('viewerKind').onchange=refreshView;
$('syncTimetable').onclick=()=>busy($('syncTimetable'),async()=>{if(!state.sync.config.semester){navigate('connection');throw new Error('请先选择学校当前学期并导入已有课表');}const result=await api('/api/timetable/sync',{method:'POST'});await loadState();toast(result.message);});
$('autoSync').onchange=()=>busy($('syncTimetable'),async()=>{await api('/api/timetable/settings',{method:'PUT',body:JSON.stringify({enabled:$('autoSync').checked,interval:60})});await loadState();});
$('mode').onchange=()=>switchMode($('mode').value).catch(e=>toast(e.message));
$('export').onclick=()=>{window.location.href=`/api/export?mode=${mode}`;};
$('samplePlan').onclick=()=>{if(active())return;draft.course_ids=['demo:photo-a','demo:music','demo:film','demo:speech'];savePlan();toast('已载入 4 门课程，包含繁忙重试与课表核验场景');};
$('refreshCatalog').onclick=()=>busy($('refreshCatalog'),async()=>{if(mode==='school'){const r=await api('/api/browser/catalog',{method:'POST'});toast(`当前页面已读取 ${r.imported} 个教学班，完整性待核实`);}await loadState();});
$('startRun').onclick=async()=>{try{await saveQueue;await api(`/api/run/start?mode=${mode}`,{method:'POST'});await loadState();navigate('monitor');}catch(e){toast(e.message);}};
$('stopRun').onclick=async()=>{try{await api('/api/run/stop',{method:'POST'});await loadState();}catch(e){toast(e.message);}};
$('resumeRun').onclick=()=>busy($('resumeRun'),async()=>{await saveQueue;await api(`/api/run/resume?mode=${mode}`,{method:'POST'});await loadState();navigate('monitor');});
$('verifyRun').onclick=()=>busy($('verifyRun'),async()=>{await api(`/api/run/verify?mode=${mode}`,{method:'POST'});await loadState();toast('已统一核验全部目标课程');});
$('resetDemo').onclick=()=>busy($('resetDemo'),async()=>{await api('/api/demo/reset',{method:'POST'});await loadState();toast('演练结果已清空，选课计划仍保留');});
$('settingsButton').onclick=()=>{if(!draft||active())return; $('scarcityFirst').checked=draft.scarcity_first;$('retries').value=draft.retries;$('retryDelay').value=draft.retry_delay;$('maxDuration').value=draft.max_duration;$('startAt').value=draft.start_at?new Date(new Date(draft.start_at).getTime()+8*3600000).toISOString().slice(0,16):'';$('settingsDialog').showModal();};
$('closeSettings').onclick=()=>$('settingsDialog').close();
$('settingsForm').onsubmit=async event=>{event.preventDefault();draft={...draft,scarcity_first:$('scarcityFirst').checked,retries:Number($('retries').value),retry_delay:Number($('retryDelay').value),max_duration:Number($('maxDuration').value),start_at:$('startAt').value?new Date($('startAt').value+'+08:00').toISOString():null};try{await savePlan();$('settingsDialog').close();toast('执行设置已保存');}catch(e){toast(e.message);}};
$('connectSchool').onclick=()=>busy($('connectSchool'),async()=>{const result=await api('/api/browser/connect',{method:'POST'});await loadState();toast(result.message);});
$('refreshCaptcha').onclick=()=>busy($('refreshCaptcha'),async()=>{await api('/api/browser/captcha/refresh',{method:'POST'});$('captchaImage').src=`/api/browser/captcha?t=${Date.now()}`;});
$('loginForm').onsubmit=async event=>{event.preventDefault();const button=event.submitter;const payload={username:$('username').value.trim(),password:$('password').value,captcha:$('captcha').value.trim()};await busy(button,async()=>{try{const result=await api('/api/browser/login',{method:'POST',body:JSON.stringify(payload)});await loadState();toast(result.message);}finally{$('password').value='';payload.password='';}});};
$('readSchoolCatalog').onclick=()=>busy($('readSchoolCatalog'),async()=>{const result=await api('/api/browser/catalog',{method:'POST'});if(mode!=='school')await switchMode('school');else await loadState();navigate('plan');toast(`已导入 ${result.imported} 个教学班`);});
$('loadSemesters').onclick=()=>busy($('loadSemesters'),async()=>{const list=await api('/api/browser/semesters');$('semesterSelect').innerHTML=list.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');});
$('importSchedule').onclick=()=>busy($('importSchedule'),async()=>{const semester=$('semesterSelect').value;if(!semester)throw new Error('请先读取并选择当前学期');const result=await api(`/api/browser/schedule/${encodeURIComponent(semester)}`,{method:'POST'});if(mode!=='school')await switchMode('school');else await loadState();navigate('schedule');toast(result.sync.message);});
$('importFile').onchange=async event=>{const file=event.target.files[0];if(!file)return;try{if(active())throw new Error('请先停止演练');if(file.size>2*1024*1024)throw new Error('课程文件请控制在 2 MB 以内');const payload=JSON.parse(await file.text());const result=await api('/api/school/import',{method:'POST',body:JSON.stringify(payload)});if(mode!=='school')await switchMode('school');else await loadState();navigate('plan');toast(`已导入 ${result.imported} 门课程和 ${result.existing} 门已有课程`);}catch(e){toast(e.message);}finally{event.target.value='';}};
setInterval(async()=>{updateCountdown();if(state)renderWorkflow();const needed=active()||pendingOperations||state?.session.browser_running||state?.session.activity.state==='running'||state?.sync.state==='running'||mode==='school'&&state?.sync.config.enabled||page==='monitor';if(needed&&!polling){polling=true;try{await loadState();}catch{showServiceError();}finally{polling=false;}}},1000);
setInterval(()=>{if(page==='monitor'&&$('browserViewDetails').open&&$('viewerAuto').checked)refreshView();},4000);
window.addEventListener('timetablechange',()=>{if(state)renderSchedule();});
window.addEventListener('resize',()=>{if(state)renderCatalog();});
let catalogResizeQueued=false;
new ResizeObserver(()=>{
  if(catalogResizeQueued)return;
  catalogResizeQueued=true;
  requestAnimationFrame(()=>{catalogResizeQueued=false;if(state&&page==='plan')renderCatalog();});
}).observe(document.querySelector('.catalog-panel .table-scroll'));
loadState().catch(error=>{toast(error.message);showServiceError();});
