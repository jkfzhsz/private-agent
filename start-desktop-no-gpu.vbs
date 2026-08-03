' Private Agent - silent launcher with GPU disabled
' Use this if the app window fails to appear (remote desktop / VM / GPU driver).
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = "D:\Private agent"
ws.Run """D:\Private agent\start-desktop.bat"" --no-gpu", 0, False
