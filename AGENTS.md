# 本地 AI 接手说明

这是已有项目，当前v0.4.0。2026-10-13已实测列表检测、当前范围目录自动导入，以及std-count单次5ID与当前14ID人数查询。已新增含Python/Chromium的Windows安装EXE、正式协议和独立默认5秒余量模块。真实提交未实现，不能以模拟演练宣称本校选课成功。

开始工作前按顺序读取：

1. `handoff/START_HERE.md`
2. `handoff/PROJECT_MEMORY.md`
3. `handoff/KNOWN_ISSUES.md`
4. `handoff/LOCAL_RUNBOOK.md`
5. `handoff/CODE_MAP.md`

当前学校范围的列表检测与自动目录导入已验收。下一项需用户明确目标教学班、核实未知时间，再从正常页面验证真实提交与核验。具体证据、疑点、验收条件见 KNOWN_ISSUES。接管现有代码，不从零重写。

复用一个正常登录的 Playwright BrowserContext 和固定 Profile；不要为每次调试重开临时上下文，不要清空 `.state`、Cookie 或 sessionStorage。学校验证失效时由用户在正常页面完成人工验证，不识别 / 绕过验证码，不伪造认证。账号密码不写进文档、源码、日志、Git 或 ZIP。

本次接手首先做只读适配。用户已要求正常进入教务、点击入口“开始选课”并读取本人课程；无需反复询问是否可以进行这些已授权操作。入口“开始选课”和课程行“选课”是不同动作；真实提交尚未实现，不把模拟演练结果当成真实选课结果。

用户已放弃Crawl4AI，改用Playwright Python、HTTPX、SQLite；无需引入Crawlee。历史版权声明保留。当前真实提交需明确目标并从正常页面核验请求与反馈，不照搬别校API。

进行有针对性的测试，保留开源版权声明。接手后在 handoff/KNOWN_ISSUES.md 和 PROJECT_MEMORY.md 记录实际确认的学校 DOM、响应字段、日期和验证结果；不要记录令牌或个人账号。新增真实接口必须从用户当前正常页面得到，不照搬其他学校的路径或固定学生 / 学期 / 批次 ID。
