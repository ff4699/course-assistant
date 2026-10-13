## 2026-10-14 / v0.4.1 通用品牌发布

用户要求移除机构名称、标志与旧缩写。当前项目名course-assistant、界面“课程助手”、安装载荷CourseAssistant、默认个人目录%LOCALAPPDATA%/CourseAssistant/data，环境变量统一COURSE_前缀，本地写接口X-Course-Client。全部旧标志/图标、含旧品牌预览图与日历图片已移除，EXE冻结使用--icon NONE；保留第三方代码实际许可证和署名。

新增assistant/connection.py及/api/browser/settings，默认空地址，用户从正常页面填写教务根地址/认证地址/门户地址；只保存本机school_connection.json。浏览器与HTTPX限定到这些地址，不猜接口、不预设机构域名。旧开发Profile、数据库、计划保持原位，必要的旧连接地址仅留私有.state配置；不公开、不重新登录。

正常页面已验证的只读接口仍为GET /course-selection-api/api/v1/student/course-select/std-count，lessonIds逗号串，result=0/data[id]三段数字字符串，仅首段含义已核实。曾实测当前范围5ID与14ID各单次请求成功，当前目录14条/1页；不能代表全校或无限批量。默认5秒、最多5目标监测、错误保留旧数据、真实提交禁用。已有课表13门中3门时间未知，未经核实不能确认无冲突。

2026-10-14连接地址3项及余量17项已通过；完整回归和新EXE发布验证正在完成。上一版本126项测试及安装回归仅是历史，不替代本版验证。新增校名/域名/标志禁止进入公开包。当前开发服务与学校会话不要为发布重启；后续适配复用原固定Profile并由用户完成人工验证。

# 本地交接包：先读这里

当前版本 v0.4.0（含浏览器的真正Windows安装EXE、正式协议、独立批量余量监测），更新日期2026-10-13。先按PROJECT_MEMORY.md、KNOWN_ISSUES.md、LOCAL_RUNBOOK.md、CODE_MAP.md顺序读顶部最新记录。当前学校范围5ID与全部14ID各一次HTTPX人数查询已实测，未来更大范围的批量上限未知；真实提交仍禁用。

用户通过了正式协议，安装EXE含Python与Chromium，源码另保留。安装默认个人数据在%LOCALAPPDATA%/CourseAssistant/data，源开发版仍使用原.state，安装包无个人Profile。默认5秒监测，限流优先；只查询和推荐，不改原志愿。实际接口及证据见docs/SEAT_MONITOR.md，安装操作见docs/WINDOWS_INSTALL.md。本轮原学校窗口已关闭后正常重连一次固定Profile，用户人工登录后，全部真实校验复用同一正常上下文，原计划保留。不要为后续开发重启当前服务或清Cookie。

已确认当前范围目录14个教学班、1页，全部时间片段可解析；课表13门，3门时间未知。查询采用当前正常页面实际出现的只读分页请求，经 HTTPX 读取并核对总数。学校默认“可选”条件只显示10条，清空该正常筛选后才得到14条；不是全校课程全集。账号/令牌/请求模板不在交接文档或 ZIP 中。

用户已同意旧视觉素材、七步指引、总耗时与响应耗时、结果反馈、成功后课表更新、停止/继续与重复启动保护，并放弃 Crawl4AI，改用 Playwright Python + HTTPX + SQLite。v0.3.1 已实现相应本地功能，真实学校提交保持禁用。学校计划由用户实时修改，开发保留其当前目标教学班；不能自行替用户决定课程。完整课表中的未知时间也必须核实后才允许无冲突判定。

源码包首次启动自动下载匹配的 Chromium；Windows x64 随浏览器包直接使用内置浏览器，但仍需要 Python3.11+ 与 Python 依赖。新电脑正常登录，现有电脑保留 `.state` 或 COURSE_STATE_DIR。不得为了开发重开临时登录上下文、删除Cookie或丢弃sessionStorage；验证码失效时由用户人工完成。

包内有源码、测试、脱敏的学校 DOM 结构 fixture、模拟界面截图、校历和版权声明。浏览器可选随包；私有数据库、Profile、账号密码、Cookie、令牌、HAR、日志、`.venv` 和 `.state` 始终排除。

启动和验证见 [../README.md](../README.md) 与 [../docs/VALIDATION.md](../docs/VALIDATION.md)。旧 HANDOFF_MANIFEST 是原始交接历史，不用于新版完整性判断；新版打包有 ZIP 校验和 SHA256 输出。

下一项是基于明确目标适配正常学校串行提交及结果核验。目前只观察了页面函数结构，尚未验证实际提交请求、反馈、选课成功后的学校课表变化；不得把演练结果作为学校成功证据。
