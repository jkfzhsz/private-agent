' Private Agent - silent desktop launcher (double-click this to start)
' Hides the console window and runs start-desktop-silent.bat in the background.
' Only the Electron app window is visible. No console window, no pause.
' NOTE: this launcher is only usable if .vbs file association is working.
' If Windows prompts "choose an app" when double-clicking, create a shortcut
' instead: target = wscript.exe  args = "D:\Private agent\start-desktop.vbs"
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = "D:\Private agent"
ws.Run """D:\Private agent\start-desktop-silent.bat""", 0, False
