'use strict';
let seatSource=null,seatSnapshot=null,seatSourceMode=null;
$('openSeats').classList.remove('seat-open');document.querySelector('.top-actions').insertBefore($('openSeats'),$('settingsButton'));
const seatCatalogDetails=document.createElement('details');seatCatalogDetails.id='seatCatalogDetails';
seatCatalogDetails.innerHTML='<summary>当前目录人数快照</summary><p id="seatCatalogStatus" class="quiet"></p><button id="seatCatalogRefresh" type="button" class="button">一次批量查询当前目录人数</button><div id="seatCatalogRows" class="seat-catalog-rows"></div>';
$('seatDialog').querySelector('.drawer-body').append(seatCatalogDetails);
let seatCatalogLoading=false;
async function loadCatalogSeats(){
  if(seatCatalogLoading)return;seatCatalogLoading=true;
  try{renderCatalogSeats(await api(`/api/seats/catalog?mode=${mode}`));}finally{seatCatalogLoading=false;}
}
function renderCatalogSeats(snapshot){
  const limit=snapshot.observed_batch_limit;
  const stale=!snapshot.updated_at||Date.now()-Date.parse(snapshot.updated_at)>Math.max(seatSnapshot?.settings.stale_after||15,(seatSnapshot?.settings.interval||5)*2)*1000;
  $('seatCatalogStatus').textContent=`当前范围 ${snapshot.rows.length} 个教学班 · 单批已核实 ${limit||'待捕获'}${snapshot.updated_at?' · 更新 '+seatTime(snapshot.updated_at)+(stale?'（已过期）':''):''}`;
  $('seatCatalogRefresh').disabled=!snapshot.rows.length||limit<snapshot.rows.length;
  $('seatCatalogRefresh').title=limit<snapshot.rows.length?'需要正常页面证明支持当前批量大小，程序不会猜测上限':'';
  $('seatCatalogRows').innerHTML=snapshot.rows.map(row=>`<div><strong>${esc(row.name)}</strong><span>${esc(row.class_code)}</span><span>剩余 ${row.remaining??'—'} / ${row.capacity??'—'} · 已选 ${row.enrolled_count??'—'}</span></div>`).join('');
}
$('seatCatalogDetails').ontoggle=()=>{if($('seatCatalogDetails').open)loadCatalogSeats().catch(e=>toast(e.message));};
$('seatCatalogRefresh').onclick=()=>busy($('seatCatalogRefresh'),async()=>renderCatalogSeats(await api(`/api/seats/catalog/refresh?mode=${mode}`,{method:'POST'})));
const seatStatusNames={idle:'未开始',running:'正在监测',paused:'已暂停',stopped:'已停止',waiting_api:'等待正常接口',waiting_login:'需要正常登录',base_information:'基础信息待核实',error:'已暂停：连续失败'};
const seatRowNames={available:'可推荐',full:'已满员',conflict:'时间待处理',stale:'数据过期 / 未查询',unknown:'人数待核实',selected:'已选成功'};
function seatTime(stamp){return stamp?new Date(stamp).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未成功更新';}
function renderSeats(snapshot){
  seatSnapshot=snapshot;
  const meta=snapshot.meta;
  $('seatModeLabel').textContent=snapshot.mode==='school'?'学校模式 · 只读批量查询，不进行真实选课':'模拟演练 · 以下余量为模拟数据';
  $('seatStatus').textContent=`${seatStatusNames[meta.phase]||meta.phase}${meta.querying?' · 查询中':''}：${meta.message}`;
  $('seatUpdated').textContent=`最后成功 ${seatTime(meta.last_success)}${meta.response_ms!=null?' · 本轮响应 '+meta.response_ms+' ms':''}${meta.next_allowed_at?' · 最早下次 '+seatTime(meta.next_allowed_at):''}`;
  $('seatStart').disabled=meta.phase==='running'||!snapshot.targets.length;
  $('seatPause').disabled=meta.phase!=='running';
  $('seatStop').disabled=['stopped','idle'].includes(meta.phase);
  $('seatRefresh').disabled=meta.querying||!snapshot.targets.length||!!meta.next_allowed_at&&Date.parse(meta.next_allowed_at)>Date.now();
  $('seatTargets').innerHTML=snapshot.targets.length?snapshot.targets.map(row=>{
    const c=row.course;
    const time=c.meetings.map(m=>`周${dayNames[m.weekday-1]} ${m.start}–${m.end}节 · ${m.weeks.join(',')}周`).join('；')||'上课时间未核实';
    const statusLabel=seatRowNames[row.status]+(row.stale&&row.status!=='stale'?' · 数据过期 / 未更新':'');
    return `<article class="seat-target ${row.stale?'seat-stale':''}"><div class="seat-target-heading"><strong>${esc(c.name)}</strong><span class="pill">${esc(statusLabel)}</span></div><p>${esc(c.class_code)} · ${esc(c.teachers.join('、')||'教师待核实')}</p><div class="seat-metrics"><span>剩余 / 容量 <b>${row.remaining??'—'} / ${row.capacity??'—'}</b></span><span>已选 <b>${row.enrolled_count??'—'}</b></span><span>基础优先级 <b>${row.base_priority}</b></span><span>动态推荐 <b>${row.recommended_rank??'—'}</b></span></div><p class="quiet">${esc(time)}</p><p class="quiet">${esc(row.reason)} · 更新 ${seatTime(row.updated_at)}</p></article>`;
  }).join(''):'<div class="empty">输入教学班代码，或填入当前计划。</div>';
}
function fillSeatSettings(snapshot){
  $('seatCodes').value=snapshot.targets.map(r=>`${r.course.class_code},${r.base_priority}`).join('\n');
  $('seatInterval').value=snapshot.settings.interval;
  $('seatStaleAfter').value=snapshot.settings.stale_after;
  $('seatStrategy').value=snapshot.settings.strategy;
  $('seatConfigDetails').open=!snapshot.targets.length;
}
async function connectSeats(fill=false){
  if(seatSource)seatSource.close();
  seatSourceMode=mode;
  const snapshot=await api(`/api/seats?mode=${mode}`);
  if(!$('seatDialog').open)return;
  renderSeats(snapshot);if(fill)fillSeatSettings(snapshot);await loadCatalogSeats();
  seatSource=new EventSource(`/api/seats/events?mode=${mode}`);
  seatSource.addEventListener('snapshot',event=>{if(seatSourceMode===mode&&$('seatDialog').open){renderSeats(JSON.parse(event.data));if($('seatCatalogDetails').open)loadCatalogSeats().catch(e=>toast(e.message));}});
  seatSource.onerror=()=>{if($('seatDialog').open)$('seatStatus').textContent='监测推送连接中断，正在重连；显示的数据仍以更新时间为准。';};
}
$('openSeats').onclick=()=>{openDialog('seatDialog');connectSeats(true).catch(e=>toast(e.message));};
$('seatDialog').addEventListener('close',()=>{seatSource?.close();seatSource=null;});
window.addEventListener('coursemodechange',()=>{if($('seatDialog').open)connectSeats(true).catch(e=>toast(e.message));});
$('seatUsePlan').onclick=()=>{
  if(!state||!draft)return;
  const selected=draft.course_ids.map(id=>state.catalog.find(c=>c.id===id));
  if(!selected.length||selected.some(c=>!c?.class_code)){toast('请先选择有明确教学班代码的计划课程');return;}
  $('seatCodes').value=selected.map((c,i)=>`${c.class_code},${i+1}`).join('\n');
};
$('seatForm').onsubmit=event=>{
  event.preventDefault();
  let targets;
  try{targets=$('seatCodes').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean).map((line,index)=>{
    const fields=line.split(',');if(fields.length>2)throw new Error('每行填写一个教学班代码和可选优先级');
    return {class_code:fields[0].trim(),priority:fields.length===2?Number(fields[1]):index+1};
  });}catch(error){toast(error.message);return;}
  busy(event.submitter,async()=>{
    if(!targets.length||targets.length>5)throw new Error('请填写1–5个教学班');
    const snapshot=await api(`/api/seats/settings?mode=${mode}`,{method:'PUT',body:JSON.stringify({targets,interval:Number($('seatInterval').value),stale_after:Number($('seatStaleAfter').value),strategy:$('seatStrategy').value,refresh_base:$('seatRefreshBase').checked})});
    renderSeats(snapshot);fillSeatSettings(snapshot);$('seatRefreshBase').checked=false;toast('已保存监测目标，原选课计划未改动');
  });
};
for(const [id,action] of [['seatStart','start'],['seatPause','pause'],['seatStop','stop'],['seatRefresh','refresh']]){
  $(id).onclick=()=>busy($(id),async()=>renderSeats(await api(`/api/seats/${action}?mode=${mode}`,{method:'POST'})));
}
setInterval(()=>{if($('seatDialog').open&&seatSnapshot){const meta=seatSnapshot.meta;$('seatRefresh').disabled=meta.querying||!seatSnapshot.targets.length||!!meta.next_allowed_at&&Date.parse(meta.next_allowed_at)>Date.now();}},1000);
