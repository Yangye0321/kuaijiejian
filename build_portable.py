# -*- coding: utf-8 -*-
"""生成「拷到别的电脑就能跑」的便携文件夹。

用法：
    python build_portable.py

产物：本目录下的 portable\\ 文件夹
    portable\\
        kuaijiejian.py            主程序
        config.json               默认配置
        start-kuaijiejian.bat     双击启动
        diagnose-kuaijiejian.bat  实机诊断
        show-keycodes.bat         键码实时显示
        运行说明.txt              给目标电脑看的说明
        python\\                   （可选）随包携带的 Python 解释器

关于 python\\ 子目录：
    脚本会把当前正在运行的 Python（sys.executable）复制/链接进去。
    能用硬链接或符号链接就链（几乎不占空间），否则回退为复制。
    若你的 Python 目录特别大又不想复制，可以：
      1) 直接跳过（目标电脑需要自己装 Python 3.8+），或
      2) 手动把整个 Python 安装目录（含 Lib、DLLs、tcl 等）拷进 portable\\python\\，
         建议用「嵌入式发行版」：python-3.x.x-embed-amd64.zip，体积只有十几 MB。
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "portable")

# 注意：便携版**不**生成图标或快捷方式。
#   * Windows 资源管理器不显示 .bat 的自定义图标（系统限制）；
#   * 用 .lnk 指定图标可以显示，但 .lnk 记的是绝对路径，文件夹一拷贝就失效，
#     反而增加困惑（用户实测过，见主目录的 icon\\ 与 make_icon.py，保留备用）。


def run_icon_tool(targets):
    """（已停用）调用 make_icon.py 生成图标并写入 bat。

    便携版不再生成图标：资源管理器不显示 .bat 的自定义图标，
    而 .lnk 又会在文件夹拷贝后因绝对路径失效。保留此函数仅供手动调用。
    """
    script = os.path.join(HERE, "make_icon.py")
    if not os.path.isfile(script):
        print("  ✗ 找不到 make_icon.py，跳过图标")
        return False
    import subprocess
    try:
        proc = subprocess.run([sys.executable, script], cwd=HERE,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        for line in proc.stdout.decode("utf-8", "replace").strip().splitlines():
            print("  %s" % line)
        return proc.returncode == 0
    except Exception as exc:
        print("  ✗ 生成图标失败：%s" % exc)
        return False

COPY_FILES = [
    "kuaijiejian.py",
    "config.json",
]

# 便携版自己生成启动脚本：先找随包的 python\，再回退到系统 PATH。
PORTABLE_LAUNCHER = """@echo off
rem ============================================================
rem  Shortcut Manager - portable launcher (ASCII only on purpose)
rem  cmd.exe reads .bat in the local ANSI codepage, so keep this
rem  file plain ASCII. Chinese text lives in kuaijiejian.py.
rem
rem  Elevation: Windows UIPI only feeds WH_KEYBOARD_LL to a normal
rem  integrity hook while the foreground app is at the same or lower
rem  integrity level, so the tool must run elevated to see keys typed
rem  into elevated windows. Re-launch elevated once (KJJ_ELEVATED
rem  guards against an infinite loop).
rem ============================================================
setlocal
cd /d "%~dp0"

if defined KJJ_ELEVATED goto :run
rem Already elevated? High integrity = S-1-16-12288, System = S-1-16-16384.
rem (whoami /groups is more reliable than fsutil, which can misfire.)
whoami /groups 2>nul | findstr /C:"S-1-16-12288" >nul 2>&1 && goto :run
whoami /groups 2>nul | findstr /C:"S-1-16-16384" >nul 2>&1 && goto :run

echo Requesting administrator privileges...
>>"%~dp0startup.log" echo [%DATE% %TIME%] ===== launcher: portable bat requesting elevation =====
rem %* is empty when double-clicked; -ArgumentList '' would be rejected.
if "%~1"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '%*'"
)
if errorlevel 1 (
    echo.
    echo   Elevation was cancelled or failed.
    echo   Without admin rights the tool will NOT receive keys typed
    echo   into windows that run as administrator.
    echo.
    pause
    exit /b 1
)
exit /b 0

:run
set "KJJ_ELEVATED=1"
set "PYW="
set "PY="

if exist "%~dp0python\\pythonw.exe" set "PYW=%~dp0python\\pythonw.exe"
if exist "%~dp0python\\python.exe" set "PY=%~dp0python\\python.exe"
if not defined PYW if exist "%~dp0pythonw.exe" set "PYW=%~dp0pythonw.exe"
if not defined PY if exist "%~dp0python.exe" set "PY=%~dp0python.exe"

if not defined PYW (
    for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do (
        if not defined PYW set "PYW=%%I"
    )
    for %%I in ("%PYW%") do if %%~zI==0 set "PYW="
)
if not defined PY (
    for /f "delims=" %%I in ('where python.exe 2^>nul') do (
        if not defined PY set "PY=%%I"
    )
    for %%I in ("%PY%") do if %%~zI==0 set "PY="
)
if not defined PYW if defined PY (
    for %%I in ("%PY%") do if exist "%%~dpIpythonw.exe" set "PYW=%%~dpIpythonw.exe"
)

rem Make sure a bundled interpreter uses its own Lib/tcl inside this folder,
rem even if this computer has another Python installed.
if exist "%~dp0python\\pythonw.exe" set "PYTHONHOME=%~dp0python"
if not defined PYTHONHOME if exist "%~dp0pythonw.exe" set "PYTHONHOME=%~dp0"

if not defined PYW if not defined PY goto :nopython

>>"%~dp0startup.log" echo [%DATE% %TIME%] ===== launcher: portable start-kuaijiejian.bat (elevated) =====

if defined PYW (
    start "ShortcutManager" /d "%~dp0" "%PYW%" "%~dp0kuaijiejian.py" %*
) else (
    start "ShortcutManager" /d "%~dp0" "%PY%" "%~dp0kuaijiejian.py" %*
)
exit /b 0

:nopython
echo.
echo   [Shortcut Manager] Python was not found.
echo.
echo   This folder has no bundled python\\ subfolder, and no python.exe
echo   was found on PATH.
echo.
echo   Option A: put a Python runtime into this folder:
echo       this folder\\python\\pythonw.exe   (see the Chinese readme)
echo   Option B: install Python 3.8 or newer and tick "Add Python to PATH".
echo.
pause
exit /b 1
"""

PORTABLE_DIAG = """@echo off
rem ASCII only - see start-kuaijiejian.bat for why.
title Shortcut Manager - Diag
cd /d "%~dp0"

set "PY="
if exist "%~dp0python\\python.exe" set "PY=%~dp0python\\python.exe"
if not defined PY if exist "%~dp0python.exe" set "PY=%~dp0python.exe"
if not defined PY (
    for /f "delims=" %%I in ('where python.exe 2^>nul') do (
        if not defined PY set "PY=%%I"
    )
    for %%I in ("%PY%") do if %%~zI==0 set "PY="
)
if not defined PY goto :nopython

echo Close the Shortcut Manager app first (only one keyboard hook at a time).
echo.
"%PY%" -u "%~dp0kuaijiejian.py" --diag %*
echo.
echo Finished. Press any key to close this window.
pause >nul
exit /b 0

:nopython
echo Python was not found. See the readme file in this folder.
pause
exit /b 1
"""

PORTABLE_KEYS = """@echo off
rem ASCII only - see start-kuaijiejian.bat for why.
title Shortcut Manager - Key Codes
cd /d "%~dp0"

set "PY="
if exist "%~dp0python\\python.exe" set "PY=%~dp0python\\python.exe"
if not defined PY if exist "%~dp0python.exe" set "PY=%~dp0python.exe"
if not defined PY (
    for /f "delims=" %%I in ('where python.exe 2^>nul') do (
        if not defined PY set "PY=%%I"
    )
    for %%I in ("%PY%") do if %%~zI==0 set "PY="
)
if not defined PY goto :nopython

echo Close the Shortcut Manager app first (only one keyboard hook at a time).
echo.
"%PY%" -u "%~dp0kuaijiejian.py" --keys %*
echo.
echo Finished. Press any key to close this window.
pause >nul
exit /b 0

:nopython
echo Python was not found. See the readme file in this folder.
pause
exit /b 1
"""

PORTABLE_UNELEVATED = """@echo off
rem ASCII only - see start-kuaijiejian.bat for why.
rem Same as start-kuaijiejian.bat but WITHOUT the UAC prompt.
rem Trade-off: UIPI means keys typed into elevated windows are not seen.
setlocal
cd /d "%~dp0"

set "PYW="
set "PY="
if exist "%~dp0python\\pythonw.exe" set "PYW=%~dp0python\\pythonw.exe"
if exist "%~dp0python\\python.exe" set "PY=%~dp0python\\python.exe"
if not defined PYW if exist "%~dp0pythonw.exe" set "PYW=%~dp0pythonw.exe"
if not defined PY if exist "%~dp0python.exe" set "PY=%~dp0python.exe"
if not defined PYW (
    for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do (
        if not defined PYW set "PYW=%%I"
    )
    for %%I in ("%PYW%") do if %%~zI==0 set "PYW="
)
if not defined PY (
    for /f "delims=" %%I in ('where python.exe 2^>nul') do (
        if not defined PY set "PY=%%I"
    )
    for %%I in ("%PY%") do if %%~zI==0 set "PY="
)
if not defined PYW if defined PY (
    for %%I in ("%PY%") do if exist "%%~dpIpythonw.exe" set "PYW=%%~dpIpythonw.exe"
)
if exist "%~dp0python\\pythonw.exe" set "PYTHONHOME=%~dp0python"
if not defined PYTHONHOME if exist "%~dp0pythonw.exe" set "PYTHONHOME=%~dp0"
if not defined PYW if not defined PY goto :nopython

>>"%~dp0startup.log" echo [%DATE% %TIME%] ===== launcher: portable start-kuaijiejian-unelevated.bat =====

if defined PYW (
    start "ShortcutManager" /d "%~dp0" "%PYW%" "%~dp0kuaijiejian.py" %*
) else (
    start "ShortcutManager" /d "%~dp0" "%PY%" "%~dp0kuaijiejian.py" %*
)
exit /b 0

:nopython
echo Python was not found. See the readme file in this folder.
pause
exit /b 1
"""

GENERATED_BATS = [
    ("start-kuaijiejian.bat", PORTABLE_LAUNCHER),
    ("start-kuaijiejian-unelevated.bat", PORTABLE_UNELEVATED),
    ("diagnose-kuaijiejian.bat", PORTABLE_DIAG),
    ("show-keycodes.bat", PORTABLE_KEYS),
]

README_TEXT = """快捷键管理工具（便携版）—— 运行说明
========================================

一、需要什么
------------
Windows 10 / 11（64 位）。至于 Python：

  * 如果本文件夹里已经有 python\\ 子目录（随包携带的解释器），
    直接双击 start-kuaijiejian.bat 即可，什么都不用装。
  * 如果没有 python\\ 子目录，就需要这台电脑装了 Python 3.8 或更高版本，
    并且安装时勾选了 "Add Python to PATH"。
    （下载：https://www.python.org/downloads/windows/ ，建议 3.11/3.12 的
      "Windows installer (64-bit)"，安装界面最下方一定要勾 Add python.exe to PATH）

二、怎么启动
------------
双击：start-kuaijiejian.bat

  会出现一个中文窗口，标题是「快捷键管理工具」。
  首次启动会在同目录生成 config.json 和 startup.log。

  **会弹一次「用户账户控制 / 无法验证发布者」，请点「是」。**
  这一条很重要，不是多余的确认：Windows 的低层键盘钩子（UIPI 规则）
  只能收到「同级或更低权限」程序的按键。工具不提升权限时，凡是以管理员
  身份运行的程序在前台，按键就完全收不到（表现为「在其他程序里按什么都没反应，
  只有工具自己的窗口能用」）。启动脚本会自动请求一次管理员权限，
  所以每次启动都会看到这个确认框。

其它几个脚本：

  start-kuaijiejian-unelevated.bat  不提权启动（不弹 UAC，但收不到管理员程序的按键）
  diagnose-kuaijiejian.bat   实机诊断：15 秒内按几下你要用的键，逐个解释
                             「为什么生效 / 为什么没生效」
  show-keycodes.bat          键码实时显示：按一下键就打印它的虚拟键码，
                             用来确认键盘发出的到底是什么键

三、默认怎么用
--------------
1. 双击 start-kuaijiejian.bat；
2. 在列表里选中规则「小键盘 . → Ctrl+Alt+小键盘0」，双击可编辑，
   确认「启用」勾上、「生效范围」按需要选择；
3. 按一下 F12：状态栏从绿色「正常输入」变成红色「替代模式（规则生效中）」，
   再按一下切回正常输入（F12 是开关：按下武装、松开才切换一次，长按不会重复切换）；
4. 应急：按 Pause 立即回到正常输入；
5. 退出：关窗口即可，键盘钩子会被卸载，键位立刻恢复原样。

注意：
  * 启动后默认是「正常输入」，不会一开机就改你的键位；
  * 触发键「小键盘 .」在 NumLock 关闭时实际发的是 Delete，
    工具默认做了归一化，按小键盘语义匹配；
  * 若目标程序以管理员身份运行（例如任务管理器），需要以管理员身份启动本工具
    才能给它注入按键；
  * 用 RawInput / 反作弊驱动直接读键的游戏可能绕过本工具，属于限制。

四、出问题怎么办
----------------
1. 先看同目录的 startup.log：记录了每次启动、钩子是否装上、
   模式切换、每次触发/透传、以及心跳（累计收到多少按键）。
2. 双击 diagnose-kuaijiejian.bat，把窗口里的输出发给开发者。
3. 若双击后闪退：startup.log 里会有完整错误堆栈；
   也可能是没装 Python（脚本会停住并提示）。

五、拷到别的电脑
----------------
直接把整个文件夹拷过去（U 盘 / 网盘都行）即可，包含：
  kuaijiejian.py、config.json、三个 .bat、运行说明.txt，以及可选的 python\\ 目录。
配置文件 config.json 也可以一起带过去，规则会原样保留。

如果拷贝过程中文件被视为「来自其他计算机」，双击时会多一个
「打开文件 - 安全警告」的确认框；在本文件夹全选 → 右键 → 属性 →
勾「解除锁定」，或执行一次：
    Get-ChildItem . -Recurse | Unblock-File
之后就不再提示。

本工具不写系统目录、不改注册表、不自动开机启动。
"""


def link_or_copy(src, dst):
    """优先硬链接/符号链接，失败则复制。返回 'link'/'copy'。"""
    try:
        os.link(src, dst)            # 硬链接：同盘、几乎不占空间
        return "link"
    except Exception:
        pass
    try:
        os.symlink(src, dst)         # 符号链接：需要权限，可能失败
        return "link"
    except Exception:
        pass
    shutil.copy2(src, dst)
    return "copy"


def copy_python(portable_dir):
    """把当前解释器（及其目录）尽量完整地放进 portable\\python\\。"""
    exe = sys.executable
    if not exe or not os.path.isfile(exe):
        print("  ✗ 取不到当前 Python 路径，跳过携带解释器")
        return False
    base = os.path.dirname(os.path.abspath(exe))
    target = os.path.join(portable_dir, "python")
    if os.path.isdir(target):
        print("  · python\\ 已存在，跳过")
        return True
    os.makedirs(target, exist_ok=True)
    linked = copied = failed = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs
                   if d not in ("__pycache__", "site-packages", "test",
                                "tests", "Doc", "doc", "share")]
        rel = os.path.relpath(root, base)
        out_dir = target if rel == "." else os.path.join(target, rel)
        os.makedirs(out_dir, exist_ok=True)
        for name in files:
            src = os.path.join(root, name)
            dst = os.path.join(out_dir, name)
            try:
                how = link_or_copy(src, dst)
                if how == "link":
                    linked += 1
                else:
                    copied += 1
            except Exception:
                failed += 1
    print("  ✓ 已携带解释器：%s（链接 %d，复制 %d，失败 %d）"
          % (base, linked, copied, failed))
    if failed:
        print("    有 %d 个文件没能放进去；若目标机启动失败，"
              "建议改用官方「嵌入式发行版」放到 portable\\python\\" % failed)
    return True


def main():
    argv = sys.argv[1:]
    with_python = "--no-python" not in argv
    print("生成便携文件夹：%s" % DEST)
    if not os.path.isdir(DEST):
        os.makedirs(DEST, exist_ok=True)

    for name in COPY_FILES:
        src = os.path.join(HERE, name)
        if not os.path.isfile(src):
            print("  ✗ 缺少源文件：%s" % name)
            return 1
        shutil.copy2(src, os.path.join(DEST, name))
        print("  ✓ %s" % name)

    for name, text in GENERATED_BATS:
        path = os.path.join(DEST, name)
        with open(path, "w", encoding="ascii", newline="\r\n") as fh:
            fh.write(text)
        print("  ✓ %s" % name)

    # 便携版不做图标/快捷方式：Windows 资源管理器不显示 .bat 的自定义图标，
    # 用 .lnk 又会因为绝对路径在拷贝后失效，反而添麻烦。
    readme = os.path.join(DEST, "运行说明.txt")
    with open(readme, "w", encoding="utf-8") as fh:
        fh.write(README_TEXT)
    print("  ✓ 运行说明.txt")

    if with_python:
        print("携带 Python 解释器（可选，方便没装 Python 的电脑）：")
        copy_python(DEST)
    else:
        print("（--no-python：不携带解释器，目标电脑需自带 Python 3.8+）")

    print("\n完成。把整个 portable 文件夹拷到目标电脑，双击 start-kuaijiejian.bat 即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
