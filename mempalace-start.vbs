' mempalace 记忆宫殿常驻服务(HTTP) 开机自启
' 登录时静默启动: mempalace-mcp --palace D:/mempalace/palace --transport http --port 8137
' 各 Agent 通过 http://127.0.0.1:8137/mcp 接入
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """D:\skills\mempalace-develop\mempalace-develop\.venv\Scripts\mempalace-mcp.exe"" --palace ""D:/mempalace/palace"" --transport http --host 127.0.0.1 --port 8137", 0, False
