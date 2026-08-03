' Private Agent - silent desktop launcher (double-click this to start)
' Hides the console window and runs start-desktop.bat in the background.
' The Electron app window is the only visible UI.
' If the app window fails to show (remote desktop / VM / GPU driver issue),
' create a copy named start-desktop-no-gpu.vbs and change the line below to:
'   ws.Run """D:\Private agent\start-desktop.bat"" --no-gpu", 0, False
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = "D:\Private agent"
ws.Run """D:\Private agent\start-desktop.bat""", 0, False
