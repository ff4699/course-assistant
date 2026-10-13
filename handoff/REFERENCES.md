# v0.3.0 当前依赖选择

用户2026-10-13决定移除Crawl4AI，使用Playwright Python1.63.0、HTTPX及Python内置SQLite，未引入Crawlee。Playwright/HTTPX许可证保存在third_party，旧Crawl4AI许可证作为历史归属保留。官网旧视觉素材来自已移除的素材地址

---

以下为历史参考。

# 参考资料与未确认项

## 已使用的GitHub项目

1. https://github.com/ShiGuangSchedule/shiguang_warehouse
   - 本校Course课表适配，MIT。字段、正常学期选择器和print-data路线已改编成Python。
   - 版权、检查过的blob SHA和改编说明在根目录 THIRD_PARTY_NOTICES.md。
   - 提供适配参考，不证明当前账号的学校响应仍相同。
2. https://github.com/USTC-XeF2/pyustc/blob/main/src/pyustc/eams/select.py
   - MIT；参考目录/人数/提交/核验分层思路。
   - 没有调用USTC的提交接口，不能把其路径当成本校路径。
3. https://github.com/Yan-Miao-miao/MyScheduler/blob/main/utils.py
   - 交叉核对Course正常课表协议字段：`lessons`、`teacherAssignmentList.person.nameZh`、`scheduleText.dateTimePlacePersonText.textZh/text`。
   - 未确认许可证，未复制源代码；原项目中的固定学生/学期/business ID没有采用。

## 已确认的 Crawl4AI（2026-10-13）

用户已明确确认 clawai 指 https://github.com/unclecode/crawl4ai 。已检查官方仓库与实际安装包，固定版本0.9.4，许可证Apache-2.0，安装包LICENSE另外要求明确署名；全文在third_party/crawl4ai/LICENSE，署名在THIRD_PARTY_NOTICES和界面“关于”。

使用官方JsonCssExtractionStrategy在内存中解析现有Playwright可见课程单元格的安全DOM投影，不另外启动AsyncWebCrawler/BrowserContext，不移交Cookie、sessionStorage或密码，不使用云端或LLM。分页由现有SchoolSession正常只读操作，不能以安装库替代学校结构核验。

官方CSS提取实现：https://github.com/unclecode/crawl4ai/blob/main/crawl4ai/extraction_strategy.py 。实际API已经在安装包和本地构造目录上验证；学校真实目录尚未实测。

## 已知学校只读协议（必须用当前账号实际值）

- `/student/home`：当前教务认证检查，退出链接作为正面证据。
- `/student/for-std/course-select`：正常进入选课应用。
- `/student/for-std/course-table`：本学期“我的课表”；学期选择器`#semesters`或`#allSemesters`。
- `/student/for-std/course-table/semester/{semester}/print-data`：活动时间表，本身不能证明完整课程。
- 页面正常触发 `/student/for-std/course-table/get-data?...semesterId=...`：观察完整lessons列表；不手工植入别人的dataId/bizTypeId。
- `/student/ws/semester/get/{semester}`：学期元数据，在正常会话中读取，有效时优先。

**选课课程目录API、真实提交API、两入口实际上下文均未在本地当前账号确认。不存在可以直接照抄的已验证Course目录端点。**

## 校历与截图

用户提供的原图完整保留在 `assistant/static/已移除的旧日历图片`，12节时刻已在 PROJECT_MEMORY 和 calendar.py记载。

`docs/*preview.png` 都是模拟界面截图；没有学校登录截图、真实学生课表截图或真实账号网络日志。原始聊天含账号密码，因此未导出聊天全文，只整理脱敏业务和技术记忆。
