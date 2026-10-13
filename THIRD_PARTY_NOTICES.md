# Third-party references and notices

## Project license and Windows executable (v0.4.0)

The project's own code is released under the root MIT LICENSE. Existing
third-party notices remain applicable; school artwork is not relicensed.
The free official release uses PyInstaller to bundle Python and dependencies,
and Inno Setup to create the installer. Their licenses and bundled dependency
license files are included under `_internal/licenses`; Chromium's own ABOUT
and accompanying license files remain in its complete browser directory.
Source and exact build dependency versions are shipped separately.

PyInstaller: https://pyinstaller.org/ ; Inno Setup: https://jrsoftware.org/isinfo.php
The PyInstaller license includes the bootloader exception allowing distribution
of bundled applications. Build-only packages do not become application services.

## ShiGuangSchedule/shiguang_warehouse

Source: https://github.com/ShiGuangSchedule/shiguang_warehouse/blob/main/resources/CUPK/cupk_01.js

Source file blob SHA inspected: `6360f3e3a98c66d21d046f9e9c0d669b02691103`.

`assistant/schedule.py` adapts its CUPK course-table route, semester selector,
activity fields and timetable-unit mapping into Python. The adapter credits
larryyan's original CUP adaptation. Our version adds shape validation, preserves
lesson IDs when provided and labels the result as scheduled-only.

v0.2 additionally implements its own conservative full-list merge and schedule
text parser. The protocol fields `lessons`, `teacherAssignmentList` and
`scheduleText.dateTimePlacePersonText` were cross-checked against the public
https://github.com/Yan-Miao-miao/MyScheduler/blob/main/utils.py reference.
No source code was copied from that project; its license was not established.
No hardcoded account, semester or business IDs from that reference are used.

MIT License

Copyright (c) 2025 星河欲转

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## USTC-XeF2/pyustc

Source: https://github.com/USTC-XeF2/pyustc/blob/main/src/pyustc/eams/select.py

Source file blob SHA inspected: `0c90a6f277a57b273377a2d21e705ae42d98ce9a`.

Reference for separating a cached lesson catalog, batched count queries,
submission and subsequent confirmation. This application does not call USTC's
submission endpoints or assume they apply to CUPK. Its scheduler is independently
implemented, bounded, sequential and checks exact target identities.

MIT License

Copyright (c) 2026 XeF2

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
## Active runtime dependencies (v0.3.0)

- Playwright Python 1.63.0: https://github.com/microsoft/playwright-python, Apache-2.0; full license preserved in third_party/playwright/LICENSE.
- HTTPX 0.28.1: https://github.com/encode/httpx, BSD-3-Clause; full license preserved in third_party/httpx/LICENSE.md.
- SQLite via Python sqlite3: https://www.sqlite.org/copyright.html (public domain dedication).
- Optional offline browser: unmodified matching Playwright Chromium release. The release's own ABOUT and other license files are included with its complete browser directory; Chromium is not branded Google Chrome. No user Profile accompanies it.

## School visual identity

Official red emblem (unmodified 04 (2).png) from the campus standard logo package https://www.cupk.edu.cn/dwxcb/upload/resources/file/2025/11/25/109063.zip, linked from https://www.cupk.edu.cn/dwxcb/c/2025-11-03/530920.shtml (accessed 2026-10-13). Used locally to identify the intended school; preserve the original shape and red artwork. School identity/artwork belongs to its rights holder and is not relicensed by this project. The UI labels this application as a local tool; this is not an official school release.

## Historical Crawl4AI attribution (inactive)

Crawl4AI 0.9.4 was removed from dependencies and integration at the user's request on 2026-10-13. Its previous Apache-2.0 license is retained in third_party/crawl4ai/LICENSE for historical attribution. No Crawl4AI or Crawlee runtime is required by v0.3.0.

Historical attribution: This product previously included software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai).
