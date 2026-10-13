## v0.4.0 安装EXE与余量监测

普通用户双击dist/Course-Assistant-Setup-0.4.0-x64.exe，默认当前用户安装。内置Python和Chromium，首次主动同意1.0协议后进入；数据%LOCALAPPDATA%/CourseAssistant/data，端口通常8766（占用后尝试9个后续端口），开发版仍8765及原.state，两者不自动搬数据。安装包不含实际账号/数据库/Profile。操作与清理见docs/WINDOWS_INSTALL.md。

开发构建：建立隔离venv后pip install -r requirements-build.lock，再python tools/build_exe.py --iscc <Inno的ISCC.exe>。绝不从含旧patchright/Crawl4AI插件的环境冻结。载荷/构建日志仅在.state/build-exe；dist有安装EXE、SHA256、公开载荷与发行清单。--skip-freeze只适用于字节码没有改变的文档/静态资源重新打包。

验证：python -m unittest discover -s tests -v；python tests/browser_smoke.py；python tests/seats_browser_smoke.py。真实EXE安装验证用python tools/verify_exe.py dist/Course-Assistant-Setup-0.4.0-x64.exe，开发验证需要pywin32/Pillow；在.state/exe-validation独立目录安装与虚构数据，不触碰实际学校Profile。Windows测试服务直接调用基础解释器加venv依赖路径，确保进程退出后数据库句柄释放，不把venv启动器当实际服务进程停止。

源码当前正常登录上下文在原服务保持。余量默认5秒、最大5个目标，刷新不重新取基础信息；只有显式配置缺失或勾选更新时读取目录。当前正常14条范围的全目录单批已实测。不要为了实测API重复开临时登录上下文，别把模拟成功写成学校选课成功。

## v0.3.1 更新（历史）

静态界面变为三入口和原生dialog侧边面板，renderShell组织主界面提醒、底部计划与准备进度；工作流/连接/计划/冲突控件维持原API契约，结果步骤日志合并。只展开学校窗口画面时定时截图。健康版本0.3.1，部署与固定Profile保持原约定。package_windows.py从pyproject.toml读取版本生成ZIP名称。

# v0.3.0 最新运行说明

此处为原生Windows开发环境，服务当前在8765运行，/api/health 应为0.3.0。已有正常学校BrowserContext仍活跃，请复用；开发本轮在原服务进程内更新自有模块保留上下文，没有开放HTTP动态执行或调试端口。正常源码包升级仍使用Ctrl+C停止后覆盖，保留Profile；重启会失去sessionStorage，恢复需要学校正常入口。

双击start.cmd首次安装依赖并自动下载匹配Chromium。Windows随浏览器ZIP含browser/browser-manifest.json和匹配二进制，启动器直接使用，无需Chrome；仍需Python3.11+与Python依赖。下载失败停止启动并保留Profile，检查网络后重试。

打包命令：python tools/package_windows.py；随浏览器版追加 --with-browser。输出dist，显式排除.state/.venv/Profile/HAR/日志。旧HANDOFF_MANIFEST仅对应原始交接历史，新版ZIP通过testzip与SHA256输出校验。

测试命令维持python -m unittest discover -s tests -v及python tests/browser_smoke.py。界面测试使用临时模拟服务和正常临时测试浏览器，完全不连接当前学校Profile；docs截图只有模拟/构造数据。

---

以下为历史记录，旧版本结论不代表当前版本。

# 本地启动与接手步骤

适用于完整源码包，不是免安装EXE。应用版本v0.2.3；真实学校页面问题见 KNOWN_ISSUES。

## Windows

1. 安装 Python 3.11+，勾选 Add python.exe to PATH。需要首次下载依赖的网络连接。
2. 完整解压，根目录应同时看到 `assistant`、`tools`、`handoff`、`start.cmd`、`requirements.txt`。不要在ZIP预览中启动，不要改成.txt。
3. 有旧服务时先按 Ctrl+C 正常停止。保留原 `.state` 或原 `COURSE_STATE_DIR`；将新代码覆盖到原目录，不清理账号状态。
4. 双击 `start.cmd` 或 `启动助手.bat`。窗口会显示三阶段进度，失败或退出会停留；日志为状态目录中的 `startup.log`。当前脚本兼容py和python，ASCII内容/CRLF换行，原生Windows尚未在云端执行验证。
5. 网页未自动打开时，手动访问 `http://127.0.0.1:8765`。窗口需要保持运行。
6. 连接学校前如缺Chromium，双击 `安装学校浏览器.cmd`。这只安装浏览器组件，不清Profile；下载失败不影响先看模拟界面。

在PowerShell中手动运行：

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m assistant --open-browser
```

已有兼容 `.venv` 时无需重建。若系统只有python命令，使用python替代py -3。旧虚拟环境损坏时仅处理 `.venv`，不要删除 `.state`。

## Linux / macOS

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m assistant --open-browser
```

桌面端通常显示学校浏览器。无显示环境采用headless；用户验证码需要在正常可交互本地窗口中完成。可通过 `COURSE_CHROMIUM` 指定正常系统Chromium路径，`COURSE_HEADLESS=1` 强制无界面，`COURSE_STATE_DIR` 指定固定状态目录。

## 先确认运行的是正确服务

`GET http://127.0.0.1:8765/api/health` 应返回 `application=course-assistant`、`version=0.2.3`。启动器会复用已有助手服务；源码覆盖后运行中的旧服务不会自动重新加载，因此升级一定先正常停止旧服务。

不要通过改端口同时打开两个使用同一Profile的浏览器进程。项目未初始化Git仓库，不依赖Git历史；需要Git时可在本地初始化，但`.state`等私有路径保持忽略。

## 正常登录与只读实测

用户此前已授权进入本人教务、选课入口和读取课程；目前希望本地AI完成真实页面适配。优先复用本地既有会话。没有有效本地会话时，在应用的正常登录表单或学校浏览器人工登录；不要把账号密码提交给交接文档或写进脚本。

开发时复用应用内部SchoolSession的context，不要让另一个爬虫进程同时启动相同Profile。若额外工具需要接入浏览器，先查其实际集成方式，确认不会另建未登录上下文或破坏Profile锁。当前没有CDP端口暴露或Crawl4AI会话桥接。

用户已经看到列表时，第一步是检查该页面而非再次登录或反复点击开始。记录frames、组件结构、真实目录响应字段、批次和学期。正常浏览器请求上的认证参数留在内存/私有状态，不打印令牌；脱敏后才制成fixture。学校课表与目录是两套数据，不混作成功证据。

登录失效时暂停自动动作，人工验证后继续。学校选课提交目前后端与UI都禁用；本轮只读适配不解除此限制。

## 测试命令

```sh
python -m unittest discover -s tests -v
node --check assistant/static/app.js
node --check assistant/static/timetable.js
python tests/browser_smoke.py
```

Windows可用 `.venv\Scripts\python.exe` 替代python。Node只用于可选JS语法检查，不是应用启动依赖。

只测相关区域可用 `python -m unittest discover -s tests -p test_selection_entry.py -v`。浏览器测试目前用系统 `chromium` / `google-chrome` 可用性判断，有些Windows机器即使装了Playwright浏览器也会被跳过；不能把跳过当通过。必要时修检测并记录实际运行条件。

所有既有测试都使用本地虚构学校页面或演练，不执行真实选课，不证明实际学校结构相同。新的本地实测结构应脱敏做成独立fixture，覆盖真实问题后才宣布修复。

## 状态与个人资料迁移

- `.state/assistant.sqlite3`：用户已有计划和课程缓存，保留在自己的电脑；包中没有此文件。
- `.state/browser-profile`：正常认证资料，保留在同一电脑和固定目录，不能上传公共仓库。
- Windows浏览器Cookie可能依赖系统加密；不要假定复制云端/Linux或另一电脑Profile就能免登录。
- 若需要迁移非认证数据，原应用右上角导出JSON：`catalog/existing/plan/coverage/run/calendar`。现有导入API主要导入catalog/existing/coverage/calendar，并不自动恢复导出的plan/run，不能称“一键完全恢复”。本地AI可以做独立数据迁移补丁，但不导出认证资料。
- 私有日志/HAR可能有个人信息，交接包没有包含它们。发生启动错误优先查看本地startup.log；共享前脱敏。

## 完整性检查

首次解压后运行 `python tools/verify_handoff.py`，核对 `HANDOFF_MANIFEST.json` 的文件大小和SHA-256。只在原始交接状态使用；开发后的合法文件变化会显示不匹配，不意味着应回滚开发成果。
