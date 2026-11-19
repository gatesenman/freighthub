; FreightHub 安装程序（NSIS 3，Unicode，中文向导，免管理员）
; 构建：makensis -DSRC=<解压后的 FreightHub 目录> -DOUT=<输出exe> installer/FreightHub.nsi
Unicode true
; 仅用于 Linux 下 Wine（只有 64 位）的安装冒烟测试：-DTARGET64=1 生成 amd64 安装器；发布版仍是 x86 安装器（与 1.4.0 一致）
!ifdef TARGET64
  Target amd64-unicode
!endif
!ifndef SRC
  !define SRC "..\stage\FreightHub"
!endif
!ifndef OUT
  !define OUT "FreightHub-Setup.exe"
!endif
!ifndef VERSION
  !define VERSION "1.5.0"
!endif
!ifdef TRIAL
  !define EDITION "（未授权版本 / 试用）"
!else
  !define EDITION ""
!endif
!define APPNAME "货代询价助手"
!define APPID "FreightHub"
!define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}"

Name "${APPNAME} ${VERSION}${EDITION}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\FreightHub"
InstallDirRegKey HKCU "Software\${APPID}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
BrandingText "${APPNAME} ${VERSION}${EDITION}"
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APPNAME} 安装程序${EDITION}"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "FreightHub"

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!define MUI_ICON "${SRC}\FreightHub.ico"
!define MUI_UNICON "${SRC}\FreightHub.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "欢迎安装 ${APPNAME}"
!define MUI_WELCOMEPAGE_TEXT "本向导将把「${APPNAME}」(FreightHub ${VERSION}${EDITION}) 安装到您的电脑。$\r$\n$\r$\n一个软件统一查询云途、中航、欣航等多个货代平台的报价。只读查询，不会下单。$\r$\n$\r$\n无需管理员权限。点击「下一步」继续。"
!define MUI_DIRECTORYPAGE_TEXT_TOP "安装程序将把 ${APPNAME} 安装到下列文件夹。如需安装到其他位置，请点击「浏览」选择。"
!define MUI_FINISHPAGE_TITLE "${APPNAME} 安装完成"
!define MUI_FINISHPAGE_TEXT "已成功安装。首次使用请到菜单「平台 → 平台账号管理」填写各货代平台的账号密码。"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "立即运行 ${APPNAME}"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function LaunchApp
  SetOutPath "$INSTDIR"
  Exec '"$WINDIR\System32\wscript.exe" "$INSTDIR\FreightHub.vbs"'
FunctionEnd

Section "主程序" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  ; 覆盖安装前清掉旧的程序文件（不动用户数据：用户数据在 %LOCALAPPDATA%\FreightHub\data）
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\site-packages"
  RMDir /r "$INSTDIR\app"
  File /r "${SRC}\*.*"
  ; 标记为安装版：数据目录改用 %LOCALAPPDATA%\FreightHub\data
  FileOpen $0 "$INSTDIR\installed.flag" w
  FileWrite $0 "installed$\r$\n"
  FileClose $0
  CreateDirectory "$LOCALAPPDATA\FreightHub\data"

  ; 快捷方式（指向 wscript 运行 vbs：无黑窗）
  SetShellVarContext current
  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$WINDIR\System32\wscript.exe" '"$INSTDIR\FreightHub.vbs"' "$INSTDIR\FreightHub.ico" 0 SW_SHOWNORMAL "" "${APPNAME} - 多平台货代询价"
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$WINDIR\System32\wscript.exe" '"$INSTDIR\FreightHub.vbs"' "$INSTDIR\FreightHub.ico" 0 SW_SHOWNORMAL "" "${APPNAME} - 多平台货代询价"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\卸载 ${APPNAME}.lnk" "$INSTDIR\Uninstall.exe"

  ; 卸载程序 + 控制面板卸载项
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\${APPID}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNKEY}" "Publisher" "FreightHub"
  WriteRegStr HKCU "${UNKEY}" "DisplayIcon" "$INSTDIR\FreightHub.ico"
  WriteRegStr HKCU "${UNKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNKEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNKEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNKEY}" "EstimatedSize" $0
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\site-packages"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\tests"
  RMDir /r "$INSTDIR\__pycache__"
  Delete "$INSTDIR\FreightHub.ico"
  Delete "$INSTDIR\FreightHub.vbs"
  Delete "$INSTDIR\run.py"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\build_exe.bat"
  Delete "$INSTDIR\start.bat"
  Delete "$INSTDIR\start-debug.bat"
  Delete "$INSTDIR\requirements.txt"
  Delete "$INSTDIR\requirements-dev.txt"
  Delete "$INSTDIR\start-server.bat"
  Delete "$INSTDIR\run-server.sh"
  Delete "$INSTDIR\Dockerfile"
  Delete "$INSTDIR\docker-compose.yml"
  Delete "$INSTDIR\部署说明.md"
  Delete "$INSTDIR\installed.flag"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNKEY}"
  DeleteRegKey HKCU "Software\${APPID}"
  ; 用户数据（账号、会话、查询历史）：静默卸载默认保留，交互卸载时询问
  IfFileExists "$LOCALAPPDATA\FreightHub\*.*" 0 done
  IfSilent done
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "是否同时删除您的用户数据（已保存的平台账号密码、查询历史）？$\r$\n$\r$\n数据位置：$LOCALAPPDATA\FreightHub$\r$\n选择「否」可保留，以后重新安装后仍可继续使用。" IDNO done
  RMDir /r "$LOCALAPPDATA\FreightHub"
done:
SectionEnd
