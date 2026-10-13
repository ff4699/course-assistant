"""Read-only DOM catalog evidence. No guessed school endpoints or credentials."""
import re
from urllib.parse import urlparse, parse_qs
from .models import Course
from .schedule import parse_schedule_text


CATALOG_VIEW = r"""() => {
    const visible=e=>e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden';
    const text=e=>(e.innerText||'').trim();
    const groups=[...document.querySelectorAll('.el-table,.ant-table-wrapper,[role="grid"]')].filter(visible);
    const roots=groups.filter(g=>!groups.some(other=>other!==g && other.contains(g)));
    roots.push(...[...document.querySelectorAll('table')].filter(t=>visible(t)&&!roots.some(g=>g.contains(t))));
    const tables=roots.map(root=>{
        const headers=[...root.querySelectorAll('thead th,[role="columnheader"]')].filter(visible).map(text);
        if(!headers.length) headers.push(...[...root.querySelectorAll('tr')].filter(visible).flatMap(r=>[...r.querySelectorAll('th')].map(text)));
        const rows=[...root.querySelectorAll('tbody tr,[role="row"]')].filter(visible).map(row=>({
            cells:[...row.querySelectorAll('td,[role="gridcell"]')].filter(visible).map(text),
            lessonId:row.getAttribute('data-lesson-id'),
            name:row.querySelector('.course-name')?.innerText.trim()||null,
            classCode:row.querySelector('.lesson-code')?.innerText.trim()||null,
            timeText:row.querySelector('.normal-dateTimePlace')?.innerText.trim()||null,
        })).filter(r=>r.cells.length>1 && !/暂无数据|暂无课程|正在加载|加载中|没有数据/.test(r.cells.join(' ')));
        return {headers,rows,empty:/暂无数据|暂无课程|没有数据/.test(text(root))};
    });
    const scope=document.querySelector('[data-batch-id],[data-turn-id]');
    const term=document.querySelector('[data-semester-id]');
    const pager=[...document.querySelectorAll('.el-pagination,.ant-pagination,[data-catalog-pagination]')].find(visible);
    const current=pager?.querySelector('.el-pager .active,.ant-pagination-item-active,[aria-current="page"]');
    const totalText=pager ? text(pager).match(/共\s*(\d+)\s*(?:条|个|门)/) : null;
    const next=pager?.querySelector('button.btn-next,.ant-pagination-next button,[aria-label="下一页"]');
    const activeSearch=[...document.querySelectorAll('input[type="search"],.el-input input,.ant-input')].filter(e=>visible(e)&&!e.readOnly).some(e=>e.value.trim());
    const schoolTable=roots.find(e=>[...e.querySelectorAll('thead th')].some(h=>text(h)==='课程信息'));
    let component=schoolTable?.__vue__, list=null, options=null;
    while(component){
        if(component.$options?.name==='AllLesson'&&component.$data?.pageInfo)list=component;
        if(component.$data?.options?.turn&&component.$data?.options?.semester)options=component.$data.options;
        component=component.$parent;
    }
    const displayColumns={operation:'操作',status:'选课状态',info:'课程信息',module:'所属课程模块',lesson:'教学班',teachers:'授课教师',time_place:'时间地点',enrollment:'已选/人数上限'};
    const schoolRows=tables.find(t=>t.headers.includes('课程信息'));
    const pickDisplay=l=>{
        const row=schoolRows?.rows.find(r=>r.classCode===l.code);
        if(!row)return null;
        const fields=Object.fromEntries(Object.entries(displayColumns).map(([key,label])=>[key,row.cells[schoolRows.headers.indexOf(label)]||'']));
        fields.verified=Object.values(displayColumns).every(label=>schoolRows.headers.includes(label));
        return fields;
    };
    const pickLesson=l=>({id:l.id,code:l.code,course:{nameZh:l.course?.nameZh,code:l.course?.code,credits:l.course?.credits},
        teachers:(l.teachers||[]).map(t=>({nameZh:t.nameZh})),limitCount:l.limitCount,
        enrolled:list?.stdCountMap?.[l.id]??null,dateTimePlace:{textZh:l.dateTimePlace?.textZh||l.dateTimePlace?.text||''},
        selected:!!l.selectedLesson,schoolDisplay:pickDisplay(l)});
    const vueLessons=schoolTable?.__vue__?.$props?.data;
    const search=list?.$refs?.search?.search||{};
    const schoolFiltered=Object.entries(search).some(([k,v])=>!k.startsWith('_')&&v!==null&&v!==undefined&&v!==''&&v!==false&&(!Array.isArray(v)||v.length>0));
    const schoolData=list&&options&&Array.isArray(vueLessons)?{
        lessons:vueLessons.map(pickLesson),semester:String(options.semester.id),batch:String(options.turn.id),
        pageInfo:{currentPage:list.pageInfo.currentPage,totalRows:list.pageInfo.totalRows,totalPages:list.pageInfo.totalPages},
        selectedLessons:(list.selectedLessons||[]).map(pickLesson),type:list.type,
        filtered:schoolFiltered,
    }:null;
    return {tables,batch:scope?.getAttribute('data-batch-id')||scope?.getAttribute('data-turn-id')||null,
        semester:term?.getAttribute('data-semester-id')||null,
        pagination:{page:current && /^\d+$/.test(text(current))?Number(text(current)):null,
            total:totalText?Number(totalText[1]):null,has_next:!!next,
            next_disabled:next?next.disabled||next.getAttribute('aria-disabled')==='true'||!!next.closest('.ant-pagination-disabled'):null,
            filtered:activeSearch},
        schoolData,
        no_batches:/暂无选课批次|没有可用的选课批次/.test(document.body?.innerText||''),
        auth_required:[...document.querySelectorAll('input[type="password"]')].some(visible) ||
            [...document.querySelectorAll('form,button,h1,h2')].filter(visible).some(e=>/^(账号登录|登录已失效|请重新登录)$/.test(text(e)))};
}"""


def normalize_catalog(view, url, identity):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    match = re.search(r"/turn/([^/]+)(?:/|$)", parsed.path)
    batch = view.get("batch") or (match[1] if match else "unverified")
    semester = view.get("semester") or query.get("semesterId", ["unverified"])[0]
    result, fields = {}, []
    for table in view["tables"]:
        headers = [re.sub(r"\s+", "", h) for h in table["headers"]]
        def value(cells, *labels):
            index = next((headers.index(label) for label in labels if label in headers), None)
            return cells[index].strip() if index is not None and index < len(cells) else ""
        if not any(label in headers for label in ("课程名称", "课程名", "课程信息")):
            continue
        fields = table["headers"]
        for row in table["rows"]:
            cells = row["cells"]
            info = value(cells, '课程信息')
            name = row.get('name') or value(cells, "课程名称", "课程名") or (info.splitlines()[0] if info else '')
            if not name:
                continue
            code = value(cells, "课程代码", "课程编码") or (info.splitlines()[1].split('|')[0] if len(info.splitlines())>1 else '')
            class_code = row.get('classCode') or value(cells, "教学班代码", "教学班名称", "教学班").split('\n')[0]
            teacher = value(cells, "授课教师", "教师", "任课教师")
            time_text = row.get('timeText') or value(cells, "上课时间", "时间", "时间地点")
            identity_scope = f'{semester}:{batch}' if semester != 'unverified' else batch
            ident = identity(identity_scope, row["lessonId"], code, class_code, name, teacher, time_text)
            numbers = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", value(cells, "已选/人数上限", "已选/上限"))
            enrolled = value(cells, "已选人数")
            capacity = value(cells, "选课人数上限", "人数上限", "容量")
            credits = re.fullmatch(r"\d+(?:\.\d+)?", value(cells, "学分"))
            if not credits and info:
                credits = re.search(r'(\d+(?:\.\d+)?)\s*学分', info)
            meetings, known = parse_schedule_text(time_text.replace("\n", ";"))
            course = Course(id=ident, name=name, code=code, class_code=class_code,
                lesson_id=row["lessonId"], teachers=[teacher] if teacher else [],
                meetings=meetings, schedule_known=known, semester=str(semester), batch=str(batch),
                entry="course-selection", source="school", location=value(cells, "上课地点", "教室"),
                credits=float(credits[1] if credits.lastindex else credits[0]) if credits else 0,
                enrolled_count=int(numbers[1]) if numbers else int(enrolled) if enrolled.isdecimal() else None,
                capacity=int(numbers[2]) if numbers else int(capacity) if capacity.isdecimal() else None,
                school_display=display_from_row(table,row))
            if ident in result and result[ident] != course:
                raise ValueError("多个课程行的教学班标识冲突，保留上次目录")
            result[ident] = course
    empty = batch != "unverified" and any(t["empty"] and any(h in {"课程名称", "课程名"}
        for h in t["headers"]) for t in view["tables"])
    pagination = view.get('pagination', {})
    for course in result.values():
        course.page = pagination.get('page')
    return {"courses": list(result.values()), "fields": fields, "batch": batch, "semester": semester,
            "pagination": pagination,
            "empty": not result and empty, "coverage": "current_page", "source": "school_dom"}


def display_from_row(table,row):
    columns={'operation':'操作','status':'选课状态','info':'课程信息','module':'所属课程模块',
             'lesson':'教学班','teachers':'授课教师','time_place':'时间地点','enrollment':'已选/人数上限'}
    headers=[re.sub(r'\s+','',h) for h in table['headers']]
    if '课程信息' not in headers:return None
    cells=row['cells']
    return {**{key:cells[headers.index(label)] if label in headers and headers.index(label)<len(cells) else '' for key,label in columns.items()},
            'verified':all(label in headers and headers.index(label)<len(cells) for label in columns.values())}


def normalize_school_data(data, view, identity):
    """Course AllLesson fields observed in the normal logged-in selection page."""
    batch, semester = str(data['batch']), str(data['semester'])
    result = []
    for lesson in data['lessons']:
        course = lesson.get('course') or {}
        if not lesson.get('id') or not course.get('nameZh'):
            raise ValueError('学校课程缺少真实教学班ID或名称')
        text = (lesson.get('dateTimePlace') or {}).get('textZh', '')
        meetings, known = parse_schedule_text(text.replace('\n', ';'))
        enrolled = lesson.get('enrolled')
        capacity = lesson.get('limitCount')
        result.append(Course(id=identity(f'{semester}:{batch}', str(lesson['id']), '', '', '', '', ''),
            lesson_id=str(lesson['id']), name=course['nameZh'], code=course.get('code') or '',
            class_code=lesson.get('code') or '', teachers=[t['nameZh'] for t in lesson.get('teachers', []) if t.get('nameZh')],
            credits=course.get('credits') or 0, meetings=meetings, schedule_known=known,
            semester=semester, batch=batch, entry=data.get('type') or 'course-selection', source='school',
            page=data['pageInfo'].get('currentPage'),
            capacity=capacity if type(capacity) is int and capacity>=0 else None,
            enrolled_count=enrolled if type(enrolled) is int and enrolled>=0 else None,
            school_display=lesson.get('schoolDisplay')))
    if len({c.id for c in result}) != len(result):
        raise ValueError('学校返回重复教学班ID，保留上次目录')
    info=data['pageInfo']
    total=info.get('totalRows')
    filtered=bool(data.get('filtered') or view['pagination']['filtered'])
    full = type(total) is int and total == len(result) and info.get('currentPage') == 1 and info.get('totalPages') == 1 and not filtered
    return {'courses':result,'fields':next((t['headers'] for t in view['tables'] if '课程信息' in t['headers']),[]),
            'batch':batch,'semester':semester,'empty':not result and total==0,
            'coverage':'complete' if full else 'current_page','source':'school_page_state',
            'entry':data.get('type'),'pages_read':1,
            'pagination':{'page':info.get('currentPage'),'total':total,'has_next':info.get('totalPages',1)>1,
                          'next_disabled':info.get('currentPage')==info.get('totalPages'),'filtered':filtered}}
