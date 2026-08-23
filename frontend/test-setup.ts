import "@testing-library/jest-dom/vitest";

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
