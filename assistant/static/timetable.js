'use strict';
window.CourseTimetable = (() => {
  const el=id=>document.getElementById(id);
  const safe=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const days=['一','二','三','四','五','六','日'];
  let selectedWeek=1, calendarKey='', latest=null, records=[];
  const identity=c=>c.lesson_id&&c.semester!=='unverified'?`${c.source}:${c.semester}:${c.lesson_id}`:c.id;
  function range(meeting,calendar){
    const start=calendar?.time_slots.find(s=>s.number===meeting.start), end=calendar?.time_slots.find(s=>s.number===meeting.end);
    return start&&end?`${start.start}–${end.end}`:`第 ${meeting.start}–${meeting.end} 节（时刻待核实）`;
  }
  function weeksText(weeks){
    const values=[...new Set(weeks)].sort((a,b)=>a-b);
    if(values.length>1&&values.every((v,i)=>!i||v===values[i-1]+1))return `第 ${values[0]}–${values.at(-1)} 周`;
    return `第 ${values.join('、')} 周`;
  }
  function calendarDate(calendar,weekday){
    const start=new Date(`${calendar.start_date}T00:00:00+08:00`);
    const offset=calendar.week_start_on_sunday?weekday%7:weekday-1;
    return new Date(start.getTime()+((selectedWeek-1)*7+offset)*86400000).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit'});
  }
  function lanes(events){
    const sorted=events.slice().sort((a,b)=>a.meeting.start-b.meeting.start||a.meeting.end-b.meeting.end);
    const groups=[];let group=[],lastEnd=0;
    for(const event of sorted){if(group.length&&event.meeting.start>lastEnd){groups.push(group);group=[];}group.push(event);lastEnd=Math.max(group.length===1?0:lastEnd,event.meeting.end);}
    if(group.length)groups.push(group);
    for(const items of groups){const ends=[];for(const event of items){let lane=ends.findIndex(end=>end<event.meeting.start);if(lane<0)lane=ends.length;ends[lane]=event.meeting.end;event.lane=lane;}for(const event of items)event.lanes=ends.length;}
    return sorted;
  }
  function detail(index){
    const record=records[index];if(!record)return;
    const course=record.course;
    el('courseDetail').innerHTML=`<h3>${safe(course.name)}</h3><dl class="course-facts"><dt>来源</dt><dd>${record.isExisting?'学校已有课程':'学生预选课程'}</dd><dt>教学班</dt><dd>${safe(course.class_code||course.lesson_id||'尚未核实')}</dd><dt>授课教师</dt><dd>${safe(course.teachers.join(' / ')||'尚未核实')}</dd><dt>地点</dt><dd>${safe(course.location||'尚未提供')}</dd></dl>${course.meetings.map(m=>`<div class="meeting-detail"><strong>周${days[m.weekday-1]} ${range(m,latest.calendar)}</strong><p>第 ${m.start}–${m.end} 节 · ${weeksText(m.weeks)}</p></div>`).join('')}${!course.schedule_known?'<div class="issue warning">此课程上课时间还未完整核实，不能据此确认无冲突。</div>':!course.meetings.length?'<p class="quiet">已确认没有固定排课。</p>':''}`;
    if(!el('courseDialog').open)el('courseDialog').showModal();
  }
  function render(state,plan){
    latest=state;
    const calendar=state.calendar, key=state.mode+calendar.start_date+calendar.semester_name;
    if(key!==calendarKey){
      calendarKey=key;
      const week=Math.floor((Date.now()-new Date(`${calendar.start_date}T00:00:00+08:00`))/604800000)+1;
      selectedWeek=week>=1&&week<=calendar.total_weeks?week:1;
    }
    selectedWeek=Math.min(Math.max(1,selectedWeek),calendar.total_weeks);
    const html=Array.from({length:calendar.total_weeks},(_,i)=>`<option value="${i+1}">第 ${i+1} 周</option>`).join('');
    if(el('weekSelect').innerHTML!==html)el('weekSelect').innerHTML=html;
    el('weekSelect').value=selectedWeek;
    el('prevWeek').disabled=selectedWeek===1;el('nextWeek').disabled=selectedWeek===calendar.total_weeks;
    el('timeSource').textContent=`时刻来源：${calendar.time_source}`;
    const combined=new Map(state.existing.map(c=>[identity(c),{course:c,isExisting:true}]));
    if(el('showPlanned').checked)for(const id of plan.course_ids){const c=state.catalog.find(c=>c.id===id);if(c&&!combined.has(identity(c)))combined.set(identity(c),{course:c,isExisting:false});}
    records=[...combined.values()];
    const events=records.flatMap((record,index)=>record.course.meetings.filter(m=>m.weeks.includes(selectedWeek)).map(m=>({...record,index,meeting:m})));
    const rowCount=Math.max(12,...calendar.time_slots.map(s=>s.number),...events.map(e=>e.meeting.end));
    el('scheduleGrid').style.gridTemplateRows=`52px repeat(${rowCount}, 80px)`;
    let content='<div class="timetable-corner">节次 / 时间</div>';
    for(let day=1;day<=7;day++)content+=`<div class="timetable-day" style="grid-column:${day+1};grid-row:1">星期${days[day-1]}<small>${calendarDate(calendar,day)}</small></div>`;
    for(let section=1;section<=rowCount;section++){
      const slot=calendar.time_slots.find(s=>s.number===section);
      content+=`<div class="timetable-period" style="grid-column:1;grid-row:${section+1}"><strong>第 ${section} 节</strong><small>${slot?slot.start+'<br>'+slot.end:'时间待核实'}</small></div>`;
      for(let day=1;day<=7;day++)content+=`<div class="timetable-cell" style="grid-column:${day+1};grid-row:${section+1}"></div>`;
    }
    for(let day=1;day<=7;day++)for(const event of lanes(events.filter(e=>e.meeting.weekday===day))){
      const c=event.course, m=event.meeting;
      const conflict=state.analysis.issues.some(issue=>issue.kind==='conflict'&&(issue.course_id===c.id||issue.other_id===c.id)&&issue.overlap.some(slot=>slot.weekday===day&&slot.weeks.includes(selectedWeek)&&slot.start<=m.end&&slot.end>=m.start));
      content+=`<button class="timetable-event ${event.isExisting?'existing':'planned'} ${conflict?'conflict':''}" data-detail="${event.index}" style="grid-column:${day+1};grid-row:${m.start+1}/span ${m.end-m.start+1};width:calc(100% / ${event.lanes} - 8px);margin-left:calc(100% * ${event.lane}/${event.lanes} + 4px)" aria-label="${safe(c.name)} ${range(m,calendar)}"><strong>${safe(c.name)}</strong><small>${safe(c.teachers.join(' / '))}</small><small>${range(m,calendar)}</small><span>${event.isExisting?'已有':'预选'}${conflict?' · 冲突':''}${!c.schedule_known?' · 时间待核实':''}</span></button>`;
    }
    el('scheduleGrid').innerHTML=content;
    el('scheduleGrid').querySelectorAll('[data-detail]').forEach(button=>button.onclick=()=>detail(Number(button.dataset.detail)));
    const missing=records.filter(record=>!record.course.meetings.length||!record.course.schedule_known);
    el('scheduleList').innerHTML=`<strong>全部已有课程：${state.existing.length} 门</strong><p>${state.existing.map(c=>safe(c.name)).join(' · ')||'尚未导入'}</p><p>当前显示第 ${selectedWeek} 周，${events.length} 个上课片段。${innerWidth<800?'左右滑动查看整周课表。':''}</p>${missing.length?'<h3>未排课 / 时间待核实</h3>'+missing.map(record=>`<div class="unplaced-course"><strong>${safe(record.course.name)}</strong><span>${record.course.schedule_known?'无固定排课':'上课时间尚未完整核实'} · ${record.isExisting?'已有课程':'预选课程'}</span></div>`).join(''):''}`;
  }
  function changeWeek(next){selectedWeek=next;if(latest)window.dispatchEvent(new Event('timetablechange'));}
  el('weekSelect').onchange=()=>changeWeek(Number(el('weekSelect').value));
  el('prevWeek').onclick=()=>changeWeek(selectedWeek-1);el('nextWeek').onclick=()=>changeWeek(selectedWeek+1);
  el('showPlanned').onchange=()=>window.dispatchEvent(new Event('timetablechange'));
  el('closeCourse').onclick=()=>el('courseDialog').close();
  return {render,range,weeksText};
})();
