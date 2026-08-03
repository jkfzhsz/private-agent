// Phase 1 Task 13 - 窗口管理(蓝图 §2.15 B2.1 补全)
//
// createWindow:
// - 挂接 preload(preload.js, 与主进程同目录)
// - contextIsolation 开启, nodeIntegration 关闭
// - dev 模式加载 VITE_DEV_SERVER_URL(vite dev server)
// - prod 模式加载构建产物 dist/index.html
import { BrowserWindow } from "electron";
import { join } from "path";

export function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 960,
    minHeight: 640,
    title: "私人智能体",
    autoHideMenuBar: true, // 隐藏默认英文菜单栏(File/Edit/View...), 界面全中文
    backgroundColor: "#eef1f8", // 与首页底色一致, 避免加载白闪
    webPreferences: {
      preload: join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // preload 需访问 process.env 判定 key 状态
    },
  });

  // 开发模式: vite dev server(由 start-dev.mjs 注入); 生产模式: 构建产物
  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    void win.loadURL(devUrl);
  } else {
    void win.loadFile(join(__dirname, "..", "dist", "index.html"));
  }

  return win;
}
