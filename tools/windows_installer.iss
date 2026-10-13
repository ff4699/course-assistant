#ifndef AppVersion
  #define AppVersion "0.4.0"
#endif
#ifndef PayloadDir
  #error PayloadDir must be supplied
#endif
#ifndef ReleaseDir
  #error ReleaseDir must be supplied
#endif

[Setup]
AppId={{04976567-F1BB-4EA3-8A5A-3365D5E2A784}
AppName=CUPK 选课助手
AppVersion={#AppVersion}
AppPublisher=CUPK Course Assistant contributors
AppComments=免费本地开源工具，非学校官方软件
DefaultDirName={localappdata}\Programs\CUPKCourseAssistant
DefaultGroupName=CUPK 选课助手
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
OutputDir={#ReleaseDir}
OutputBaseFilename=CUPK-Course-Assistant-Setup-{#AppVersion}-x64
SetupIconFile={#PayloadDir}\_internal\assistant\static\app.ico
UninstallDisplayIcon={app}\CUPKCourseAssistant.exe
VersionInfoDescription=CUPK 选课助手安装程序
VersionInfoProductName=CUPK Course Assistant
VersionInfoVersion={#AppVersion}
CloseApplications=no
RestartApplications=no
SetupMutex=CUPKCourseAssistantInstaller
AppMutex=Local\CUPKCourseAssistant-Desktop
UninstallDisplayName=CUPK 选课助手

[Languages]
Name: "zhcn"; MessagesFile: "compiler:Default.isl"

[LangOptions]
LanguageName=简体中文
LanguageID=$0804
DialogFontName=Microsoft YaHei UI
DialogFontSize=9

[Messages]
SetupAppTitle=安装 — CUPK 选课助手
SetupWindowTitle=安装 — CUPK 选课助手
UninstallAppTitle=卸载 — CUPK 选课助手
UninstallAppFullTitle=卸载 — CUPK 选课助手
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonCancel=取消
ButtonFinish=完成(&F)
WelcomeLabel1=欢迎安装 [name]
WelcomeLabel2=这将在你的电脑安装 [name/ver]。%n%n程序免费，包含 Python 运行环境和 Chromium，无需另装谷歌浏览器。首次启动需阅读并同意使用协议。%n%n学校登录与课程读取需要联网，真实选课提交尚未启用。
WizardSelectDir=选择安装位置
SelectDirDesc=请选择程序的安装位置。
SelectDirLabel3=个人课表、计划和会话单独保存在本机用户数据目录，升级不会删除。
WizardSelectTasks=附加任务
SelectTasksDesc=选择需要的快捷方式。
SelectTasksLabel2=请选择安装时执行的附加任务：
WizardReady=准备安装
ReadyLabel1=即将安装 [name]。
ReadyLabel2a=点击“安装”继续，或点击“上一步”修改设置。
WizardInstalling=正在安装
InstallingLabel=正在安装 [name]，请稍候。
FinishedHeadingLabel=安装完成
FinishedLabelNoIcons=已安装 [name]。可通过开始菜单打开助手。
FinishedLabel=已安装 [name]。可通过桌面或开始菜单打开助手，首次使用请主动同意协议。
ClickFinish=点击“完成”退出安装程序。
ConfirmUninstall=确认卸载 %1 及其程序文件？%n%n个人课表、计划和浏览器会话会保留。彻底清理的方法见安装目录的“安装与使用说明.txt”。
UninstalledAll=%1 已卸载。%n%n本机个人数据已保留，如不再使用可按说明自行清理。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\CUPK 选课助手"; Filename: "{app}\CUPKCourseAssistant.exe"; Tasks: desktopicon
Name: "{group}\CUPK 选课助手"; Filename: "{app}\CUPKCourseAssistant.exe"
Name: "{group}\使用说明"; Filename: "{app}\安装与使用说明.txt"
Name: "{group}\卸载 CUPK 选课助手"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\CUPKCourseAssistant.exe"; Description: "启动 CUPK 选课助手"; Flags: nowait postinstall skipifsilent

