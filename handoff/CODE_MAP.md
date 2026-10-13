## v0.4.1连接配置
assistant/connection.py验证并持久化本机地址；GET/PUT /api/browser/settings读取/保存。BrowserSession按配置域名允许正常浏览及只读查询；默认空配置不会打开网站。环境变量COURSE_、本地请求头X-Course-Client、前端CourseTimetable、course-*存储键。无机构素材或自定义EXE图标。

## v0.4.0 新增

- assistant/consent.py：正式协议内容/摘要、本机版本与同意时间、合并密码保存/自动填充关闭偏好，不清Cookie。
- tools/desktop_entry.py：冻结入口、Tk同意门禁、单实例、独立数据目录、本地服务、内置UI浏览器与正常退出；没有生产测试同意绕过。
- tools/build_exe.py、windows_installer.iss、requirements-build.lock：隔离环境冻结、完整Chromium、第三方许可、公开载荷校验、中文安装器与SHA256。tools/verify_exe.py为虚构数据安装回归。
- assistant/counts.py：观察实际std-count URL/方法/参数/响应包装，内存认证模板、每轮当前Cookie、HTTPX连接池、单请求及限流，最大5目标或已核实目录单批。
- assistant/seat_monitor.py：缓存目标/人数、5秒开始时间排期、单任务与互斥、退避、过期、原顺序之外的推荐、SSE通知、共享目录快照；从不提交。
- app.py：/api/seats系列、SSE、全目录手动查询、协议与版权入口、冻结模式核验同意。browser.py注册被动人数观察并跟随最近正常查询的页。
- static/seats.js：原生侧边面板及SSE；当前计划填入不改原计划。tests/test_seats.py（17项）、seats_browser_smoke.py、test_consent.py新增针对性验证。
- docs/USER_AGREEMENT.md、WINDOWS_INSTALL.md、SEAT_MONITOR.md为正式协议、用户流程及实际学校字段/验证边界。

## v0.3.1 更新（历史）

静态界面变为三入口和原生dialog侧边面板，renderShell组织主界面提醒、底部计划与准备进度；工作流/连接/计划/冲突控件维持原API契约，结果步骤日志合并。只展开学校窗口画面时定时截图。健康版本0.3.1，部署与固定Profile保持原约定。package_windows.py从pyproject.toml读取版本生成ZIP名称。

## 目录完整信息与冲突摘要更新

models.py新增SchoolDisplay（8列白名单文本）与conflict_summary；冲突按唯一教学班对统计，合并相同星期节次的周次。catalog.py按classCode关联原列，HTTPX保留当前已核实列。app.js按学校原列完整显示、模块/限制/地点可搜索，冲突分列两类并显示双方班号和全部重叠时段。学校操作列仅文本，额外本地预选列，不开启真实提交。

# v0.3.0 代码变化

- catalog.py 的 CATALOG_VIEW 识别实际 Element 表格，normalize_school_data 解析白名单 Vue 课程信息及 pageInfo；删除 crawl4ai_bridge.py。
- browser.py 被动观察当前正常只读查询响应，内存模板驱动 HTTPX 分页；refresh_selection 检查真实状态，认证失败不让旧DOM恢复就绪。
- catalog_sync.py 自动落库，已完整目录避免重复全量读取。
- workflow.py 提供证据驱动的7步guide和run_summary；app.py GET /api/state 返回guide/run.summary；POST /api/run/resume 仅支持demo，school返回409。
- engine.py 累计实际执行耗时、每次response_ms/verification_ms，成功回调、停止继续与任务互斥；真实学校适配器尚未实现。
- static/index.html/app.js/ui.css 显示学校标识、7步、结果/耗时、恢复按钮；已移除的旧视觉素材 为官网素材。
- tools/browser_runtime.py 解析匹配版本的随包浏览器；windows_launcher.py 首次自动安装Chromium；package_windows.py 显式公开目录打包，排除所有私有状态。
- 测试新增真实脱敏DOM结构、学校JSON字段、HTTPX分页、工作流与耗时/恢复、浏览器部署和失败保存Profile。

---

以下为历史记录，旧版本结论不代表当前版本。

# 源码和接口索引

2026-10-13 / v0.2.3新增：

- `catalog.py`：可见表格/分离表头/grid、批次/学期/分页证据，规范化课程和保守时间解析。
- `crawl4ai_bridge.py`：Crawl4AI JsonCssExtractionStrategy解析可见课程单元格的安全投影，只在内存执行，不管理浏览器。
- `catalog_sync.py`：3秒只读观察已有SchoolSession、自动目录落库、同批次部分页合并、错误和时间、有限自动批量分页。新存储键`school:catalog_sync`。
- `browser.refresh_selection`：观察手动导航，检测/读取共用`catalog_snapshot`，不启动或刷新浏览器。`read_all_catalog`：学校分页控件匹配后逐页读取，并核对总数；不操作选课按钮。
- GET state/status会节制观察真实页面，连接/登录后也观察并导入目录；`POST /api/browser/catalog/all`手动重试全量读取；`GET /api/browser/diagnostics`只返回选课frame结构摘要，不返回HTML、URL、账号或令牌。
- 前端`ui.css`负责全宽和黑白主题；app.js中的目录分页只操作已保存目录。学校真实分页控件仍需现场确认。

以下调用链描述为v0.2.2基线历史，最新行为以上面说明及源码为准。

基线v0.2.2；使用相对路径和函数名，避免依赖云端绝对路径。

## 关键文件

| 文件 | 作用 / 接手重点 |
| --- | --- |
| `assistant/browser.py` | SchoolSession、Profile锁、正常登录、入口点击、截图、学校课表和目录；P0的主要修复位置 |
| `assistant/app.py` | FastAPI、生命周期、API、缓存和模式；连接后未自动读取目录的调用链在此 |
| `assistant/static/app.js` | 计划列表、连接文案、状态轮询、模式切换和读取按钮 |
| `assistant/static/index.html` | 页面DOM与操作控件 |
| `assistant/static/timetable.js` | 周课表、节次时刻、重叠分列、详情 |
| `assistant/static/style.css` | 桌面/手机样式 |
| `assistant/models.py` | Course/Meeting/Plan、精确周次冲突、重试0–10、最多5课 |
| `assistant/schedule.py` | 课表规范化、保守时间文本解析、全列表/活动合并和完整性 |
| `assistant/sync.py` | 单个课表同步器、60秒更新、失败保存旧快照 |
| `assistant/engine.py` | 模拟顺序执行、共享核验、重试、停止和恢复 |
| `assistant/demo.py` | 9门演练课、2门预置课、模拟提交；不是本校适配器 |
| `assistant/calendar.py` | 用户校历时刻、学校元数据覆盖 |
| `assistant/activity.py` | 当前实际步骤与状态，无伪造百分比 |
| `assistant/store.py` | SQLite KV：表state，字段key/value |
| `assistant/__main__.py` | 本机端口、已有服务复用、浏览器打开 |
| `tools/windows_launcher.py` | 仅标准库引导，创建/检查venv、依赖和启动日志 |
| `start.cmd`、`启动助手.bat` | Windows入口，ASCII/CRLF，启动失败退出停留 |
| `安装学校浏览器.cmd` | 独立安装Chromium组件，不触碰Profile |
| `tools/verify_handoff.py` | 新增的只读交接文件完整性检查，不属于应用逻辑 |

## 两条当前调用链

连接：

```text
前端connectSchool/loginForm
  -> POST /api/browser/connect 或 /login
  -> SchoolSession.connect/login
     -> _ensure + 正常认证检查
     -> _open_selection -> _enter_course_list
        -> SELECTION_VIEW（当前只支持主文档普通表格）
  -> import_current_if_known（仅已有课表，不导入选课目录）
  -> 前端loadState -> renderConnection
```

手动目录：

```text
前端readSchoolCatalog / refreshCatalog
  -> POST /api/browser/catalog
  -> read_catalog -> connect -> 就绪检查 -> 当前表格提取
  -> store.put('school:catalog', ...)
  -> switchMode('school') 或 loadState
  -> renderCatalog 读取state.catalog
```

`GET /api/state` 的session来自public_status缓存，不会重新识别学校页面。目录检测、读取和状态刷新必须统一实际数据来源，修复时不要只改文案。

## API

| 请求 | 用途 |
| --- | --- |
| GET `/api/health` | 应用标识、版本 |
| GET `/api/state?mode=school` | 目录、已有课程、计划、分析、会话、校历、同步、能力 |
| PUT `/api/plan?mode=school` | 保存预选/优先级和执行参数 |
| POST `/api/browser/connect` | 复用会话并进入选课入口/列表 |
| POST `/api/browser/login` | 用户填写的正常认证表单，敏感信息不回显 |
| GET `/api/browser/status` | 缓存的会话/选课状态 |
| GET `/api/browser/view?kind=selection` | 当前学校画面；timetable类型显示课表标签页 |
| GET `/api/browser/semesters` | 当前账号正常课表页提供的学期选项 |
| POST `/api/browser/schedule/{semester}` | 导入该真实学期的已有课表 |
| POST `/api/timetable/sync` | 立即更新配置学期课表 |
| PUT `/api/timetable/settings` | 自动同步enabled及interval，30–300秒，界面默认60 |
| POST `/api/browser/catalog` | 当前人工触发的选课目录读取/落库 |
| POST `/api/school/import` | 导入课程JSON；示例不是实际课程 |
| GET `/api/export?mode=school` | 导出非认证课程/计划/校历等JSON |
| POST `/api/run/start?mode=demo` | 模拟执行；school模式409 |
| POST `/api/run/stop`、`/api/run/verify` | 停止/核验模拟任务 |

非GET写请求需 `X-Course-Client: local` 且同Origin；服务只绑定127.0.0.1。调试不能为图方便去掉这些检查。浏览器_guard只限制正常学校域名及未知变更请求，实际新增只读接口要根据正常页面请求验证。

## 存储键

`demo:selected`、`demo:run`、`demo:plan`；`school:catalog`、`school:existing`、`school:plan`、`school:coverage`、`school:calendar`、`school:sync`、`school:sync_config`。

Course包含本地id、真实lesson_id（已知时）、code、class_code、teachers、meetings、schedule_known、semester、batch、entry、capacity、enrolled_count、page、location、required、source。remaining仅由容量和已选人数都已知时计算。Meeting含weekday1–7、起止节次1–24、明确周次数组1–60。真实目录目前的semester/time尚未核实。

## 测试对应

- `test_core.py`：冲突/计划/执行器/恢复。
- `test_api.py`：本地API、模式隔离、敏感错误不回显、导入/导出/上限。
- `test_browser_session.py`：正常本地Cookie持久性、学期、正常课表响应与截图。
- `test_selection_entry.py`：入口点击、普通表格、SPA延时、popup、禁用/多入口/空列表；未包含实际学校分离表头/iframe等结构。
- `test_timetable.py`：时刻、时间解析、完整课程合并、同步失败保留、画面独立锁。
- `test_launcher.py`：启动器6项本地逻辑测试，非原生cmd.exe运行。
- `browser_smoke.py`：独立临时服务、真实Chromium界面、4门模拟成功、冲突、0–10重试和390px布局；无真实学校访问。
