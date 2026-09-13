import "@testing-library/jest-dom/vitest";
// 2026-09-12: findBy*/waitFor 默认异步等待 1s, 在 vitest 多文件并行负载下
// HomeView 等懒加载 chunk 的渲染可能超时(App.test AC-17 实测)。统一放宽到
// 3s —— 只影响"等多久", 不改变任何断言语义; 元素真不出现时仍会失败。
import { configure } from "@testing-library/react";

configure({ asyncUtilTimeout: 3000 });

// jsdom 未实现 canvas getContext(会抛 Not implemented), 测试环境 stub 为 null
// 组件内已有 try/catch 防御, 这里保证测试环境稳定
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.getContext = function getContext() {
    return null;
  } as typeof HTMLCanvasElement.prototype.getContext;
}

// jsdom 未实现 matchMedia(部分环境), stub 默认 no-preference
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}
