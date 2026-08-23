# FlowSpace 桌面应用 — UI 设计系统

> 版本：v2.1 | 设计方向：浅色蓝紫粉渐变 · 磨砂玻璃 · 有机液体质感 | 更新：2026-07-29

---

## 1. 设计哲学

### 1.1 核心美学定位

| 维度 | 取向 |
|------|------|
| **氛围** | 轻盈、流动、现代、精致 |
| **情绪关键词** | 柔和 / 通透 / 有机 / 呼吸感 |
| **差异化特征** | 磨砂玻璃叠加有机液体动效，避免常规扁平化或纯毛玻璃的泛 AI 审美 |
| **适用场景** | 创意工具、设计协作平台、个人工作台、品牌管理后台 |

### 1.2 设计原则

- **层次优先**：通过模糊 + 透明度 + 阴影构建清晰的前后景关系，而非依赖边框
- **液体生命感**：背景 blob 持续有机运动，传递"流动的数据"隐喻
- **触感反馈**：所有可交互元素都有微动效响应（hover 上浮、点击涟漪、波纹拖尾）
- **克制配色**：蓝紫粉渐变仅用于背景层和装饰，UI 主体保持白/灰中性色，避免视觉疲劳
- **无障可达**：符合 WCAG AA，支持 `prefers-reduced-motion`，键盘完全可操作

---

## 2. 色彩系统

### 2.1 语义色板

| Token | Hex | 用途 |
|-------|-----|------|
| `--text-primary` | `#1e293b` | 标题、正文主色 |
| `--text-secondary` | `#64748b` | 辅助文字、描述 |
| `--text-tertiary` | `#94a3b8` | 占位符、元信息、禁用态 |

### 2.2 磨砂玻璃色板

| Token | 值 | 用途 |
|-------|-----|------|
| `--glass-bg` | `rgba(255,255,255,0.55)` | 面板/卡片背景 |
| `--glass-border` | `rgba(255,255,255,0.7)` | 面板/卡片边框 |
| `--glass-shadow` | `0 8px 32px rgba(148,163,184,0.15)` | 面板/卡片投影 |

### 2.3 背景液体 Blob 调色盘

| 名称 | RGB | 视觉 |
|------|-----|------|
| Sky Blue | `168, 216, 255` | 天空蓝 |
| Blue | `147, 197, 253` | 中蓝 |
| Purple | `196, 181, 253` | 淡紫 |
| Violet | `216, 180, 254` | 薰衣草紫 |
| Pink | `249, 168, 212` | 樱花粉 |
| Light Pink | `251, 191, 220` | 浅桃粉 |

### 2.4 功能色

| 用途 | 背景 | 文字 | 图表示例 |
|------|------|------|---------|
| 成功/正向 | `#d1fae5` | `#059669` | 绿色渐变图标 |
| 警告/审核 | `#fef3c7` | `#d97706` | — |
| 信息/进行中 | `#d1fae5` | `#059669` | 蓝色渐变图标 |
| 错误/下降 | — | `#ef4444` | — |
| 草稿/中性 | `#f1f5f9` | `#64748b` | — |

### 2.5 品牌装饰色（按钮/标签/强调）

| 名称 | 渐变 | Hex 端点 |
|------|------|---------|
| Indigo | 线性 135deg | `#818cf8` → `#6366f1` |
| Violet | 线性 135deg | `#c084fc` → `#a855f7` |
| Pink | 线性 135deg | `#f472b6` → `#ec4899` |
| Logo | 线性 135deg | `#818cf8` → `#c084fc` → `#f472b6` |

---

## 3. 字体系统

### 3.1 字体选型

| 用途 | 字体 | 权重 | 字号范围 |
|------|------|------|---------|
| 全局主字体 | **Space Grotesk** | 300–700 | 10px–32px |
| 等宽回退 | `ui-monospace, SFMono-Regular, monospace` | 400 | — |
| 系统回退 | `system-ui, -apple-system, sans-serif` | — | — |

**选择理由**：Space Grotesk 兼具几何现代感与温和的人文气息，避免 Inter/Roboto 的过度使用。字符辨识度高，适合仪表盘密集信息场景。

### 3.2 排版层级

| 层级 | 字号 | 字重 | 字间距 | 用途 |
|------|------|------|--------|------|
| H1 | 26px | 700 | `-0.03em` | 页面主标题（问候语） |
| H2 | 18px | 700 | `-0.02em` | Logo 文字 |
| H3 | 16px | 600 | `-0.02em` | 面板标题 |
| Body L | 14px | 500–600 | `-0.01em` | 列表项、导航项 |
| Body M | 13px | 400–600 | — | 动态文本、任务项 |
| Body S | 12px | 500 | — | 元信息、统计标签 |
| Caption | 11px | 400–500 | — | 角色标签、时间戳 |
| Label | 10px | 600 | `0.08em` | 导航分类标签（大写） |
| Stat Value | 32px | 700 | `-0.03em` | 统计数值 |

### 3.3 行高规范

| 上下文 | 行高 | 说明 |
|--------|------|------|
| 标题 | `1.2` | 紧凑 |
| 正文 | `1.5` | 舒适阅读 |
| 任务列表 | 继承 | 单行 |

---

## 4. 空间系统

### 4.1 基础栅格

基于 4px 基准单元，所有间距为该基准的整数倍。

| Token | 值 | 用途 |
|-------|-----|------|
| `--space-2` | 8px | 元素内紧缩间距、badge padding |
| `--space-3` | 12px | 容器间距、卡片 gap、列表项间距 |
| `--space-4` | 16px | 面板 padding、区块 gap |
| `--space-6` | 24px | 面板内 padding（大） |

### 4.2 圆角规范

| Token | 值 | 用途 |
|-------|-----|------|
| `--radius-sm` | 12px | 图标容器、头像、输入框、导航项 |
| `--radius-md` | 16px | 统计卡片、色板卡片 |
| `--radius-lg` | 24px | 侧边栏、顶栏、内容面板 |

### 4.3 布局网格

```
┌──────────────────────────────────────────────────┐
│ 12px padding                                      │
│ ┌─────────┬────────────────────────────────────┐ │
│ │ Sidebar │ Topbar                             │ │
│ │ 260px   ├────────────────────────────────────┤ │
│ │         │ ┌──────────┬──────────┬──────────┐ │ │
│ │         │ │ Stat 1   │ Stat 2   │ Stat 3   │ │ │
│ │         │ ├──────────┴──────────┴──────────┤ │ │
│ │         │ │ Project List  │  Tasks/Activity │ │ │
│ │         │ │   (1.5fr)    │    (1fr)        │ │ │
│ │         │ └─────────────────────────────────┘ │ │
│ └─────────┴────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

**响应式断点：**

| 断点 | 布局变化 |
|------|---------|
| > 1200px | 完整 4 列统计 + 双栏内容 |
| 768–1200px | 2 列统计 + 单栏内容 |
| < 768px | 隐藏侧边栏，2 列统计，面板间距缩小 |

---

## 5. 磨砂玻璃组件规范

### 5.1 标准玻璃面板

```css
.glass-panel {
  background: rgba(255, 255, 255, 0.55);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: 24px;
  box-shadow: 0 8px 32px rgba(148, 163, 184, 0.15);
  padding: 24px;
}
```

### 5.2 侧边栏玻璃

与标准面板相同，但 `backdrop-filter` 增强至 `blur(24px)`，提高与背景的分离度。

### 5.3 统计卡片玻璃

```css
.stat-card {
  /* 同 glass-panel */
  border-radius: 16px;      /* 较小圆角 */
  padding: 20px 24px;        /* 紧凑内边距 */
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 40px rgba(148, 163, 184, 0.2);
}

.stat-card::after {
  /* 渐变遮罩层，hover 时渐显 */
  background: linear-gradient(
    135deg,
    rgba(129, 140, 248, 0.04),
    rgba(244, 114, 182, 0.04)
  );
  opacity: 0 → 1;
}
```

### 5.4 关键约束

- **禁止**纯黑 `#000` 或纯白 `#fff` 作为玻璃背景
- **禁止**在玻璃面板上叠加玻璃子面板（嵌套模糊会导致性能问题和视觉混乱）
- 玻璃边框必须为 `rgba(255,255,255,0.7)` 以上，确保在浅色背景上可见边界
- `saturate(180%)` 是必须的——防止 blur 导致的颜色"洗白"

---

## 6. 有机液体动效系统

### 6.1 架构总览

```
requestAnimationFrame 循环
├── AmbientWave.draw()     ← 底部正弦波环境线
├── Blob.update()          ← 每个 blob 更新位置 + 呼吸
├── drawWavyBlob()         ← 波浪边缘变形绘制
├── WaterRipple.draw()     ← 点击涟漪渲染
└── TrailRipple.draw()     ← 鼠标拖尾渲染
```

### 6.2 Blob 有机漂浮

| 参数 | 值 | 说明 |
|------|-----|------|
| 数量 | 7 个 | 覆盖全屏不拥挤 |
| 半径范围 | 80–260px | 大小不一增强层次 |
| 基础速度 | ±0.35px/帧 | 缓慢漂移 |
| 正弦振幅 X | 30–90px | 水平有机摆动 |
| 正弦振幅 Y | 20–70px | 垂直有机摆动 |
| 呼吸系数 | 1 ± 0.06 × sin() | 半径微呼吸 |
| 颜色 | 6 色调色盘随机 | 见 §2.3 |
| 透明度范围 | 0.35–0.60 | 柔和不过度 |

### 6.3 波浪边缘变形

每个 blob 不再绘制为完美圆形，而是通过叠加多个频率的正弦波使边缘自然起伏：

```
distortion = r
  + sin(angle × 5 + time × 0.002) × 8~22px        ← 主波纹
  + cos(angle × 3 + time × 0.003 + φ) × (4~11px)   ← 次波纹
  + sin(angle × 7 + time × 0.004) × (2~5.5px)       ← 细节波纹
```

- 分段数：64 段
- 内层高亮环：白色 `rgba(255,255,255,0.2)` 描边，半径缩小 60%

### 6.4 点击涟漪

| 属性 | 规范 |
|------|------|
| 触发 | `document.click` 任意位置 |
| 层数 | 4 个同心环 |
| 逐层延迟 | 每层 12% 相位偏移 |
| 最大半径 | 120–200px 随机 |
| 生命周期 | 2800ms |
| 透明度衰减 | `(1 - progress) × 0.35 × (1 - layerIndex × 0.18)` |
| 线宽 | 2.5px → 0.5px 线性衰减 |
| 颜色 | 6 色调色盘随机 |

### 6.5 鼠标拖尾涟漪

| 属性 | 规范 |
|------|------|
| 触发间隔 | 每 180ms |
| 最大半径 | 50px |
| 生命周期 | 1800ms |
| 透明度衰减 | `(1 - progress)² × 0.28`（二次衰减更快消失） |
| 数量上限 | 30 个（超出删除最早） |

### 6.6 底部环境波

3 条重叠正弦波在屏幕下部 82% 位置浮动：

| 层 | 振幅 | 频率 | 速度 | 颜色 | 线宽 |
|----|------|------|------|------|------|
| 1 | 16px | 0.0035 | 0.0005 | `rgba(147,197,253,0.12)` | 2px |
| 2 | 22px | 0.0028 | 0.00035 | `rgba(196,181,253,0.10)` | 3px |
| 3 | 12px | 0.0042 | 0.0006 | `rgba(249,168,212,0.09)` | 1.5px |

Y 轴基准线本身也在 `h × 0.82 + sin(time × 0.00015) × 30` 范围内缓慢上下浮动。

### 6.7 性能约束

| 指标 | 限制 |
|------|------|
| requestAnimationFrame | 单循环，无嵌套 RAF |
| Blob 数量 | 7 个 |
| 涟漪上限 | 不限（自然衰减移除） |
| 拖尾上限 | 30 个 |
| 鼠标采样率 | 180ms 间隔 |
| Canvas 指针事件 | `pointer-events: none` |
| 移动端降级 | `prefers-reduced-motion` 全局禁用动画 |

---

## 7. UI 组件库

### 7.1 导航项

```css
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  font-size: 14px; font-weight: 500;
  color: #64748b;
  transition: all 0.25s cubic-bezier(0.16,1,0.3,1);
}

.nav-item:hover  { background: rgba(255,255,255,0.6); color: #1e293b; }

.nav-item.active {
  background: rgba(255,255,255,0.8);
  color: #7c3aed; font-weight: 600;
  box-shadow: 0 2px 8px rgba(139,92,246,0.1);
}

.nav-item.active::before {
  /* 左侧渐变指示条 */
  content: ''; position: absolute; left: 0;
  width: 3px; height: 20px;
  background: linear-gradient(180deg, #818cf8, #c084fc);
  border-radius: 0 3px 3px 0;
}
```

### 7.2 按钮

| 变体 | 背景 | 边框 | Hover |
|------|------|------|-------|
| **图标按钮** | `rgba(255,255,255,0.5)` | `rgba(148,163,184,0.15)` | `rgba(255,255,255,0.8)` + 边框加深 |
| **面板操作** | 透明 | 无 | `rgba(139,92,246,0.08)` 紫底 |
| **Badge** | `linear-gradient(135deg, #818cf8, #c084fc)` | 无 | 静态 |

### 7.3 输入框

```css
.search-input {
  padding: 10px 16px 10px 40px; /* 左侧留图标空间 */
  border-radius: 12px;
  border: 1px solid rgba(148,163,184,0.2);
  background: rgba(255,255,255,0.6);
  transition: all 0.25s cubic-bezier(0.16,1,0.3,1);
}

.search-input:focus {
  border-color: #a78bfa;
  background: rgba(255,255,255,0.85);
  box-shadow: 0 0 0 3px rgba(167,139,250,0.1);
  outline: none;
}
```

### 7.4 复选框

| 状态 | 样式 |
|------|------|
| 默认 | 20×20px，圆角 6px，`border: 2px solid rgba(148,163,184,0.3)` |
| Hover | 边框变为 `#a78bfa` |
| 完成 | 紫粉渐变填充，白色对勾 `::after` |
| 关联文字 | `text-decoration: line-through; color: #94a3b8` |

### 7.5 项目列表项

```css
.project-item {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 16px; border-radius: 12px;
  transition: all 0.25s cubic-bezier(0.16,1,0.3,1);
}
.project-item:hover { background: rgba(255,255,255,0.5); }
```

### 7.6 状态标签

| 状态 | 背景 | 文字色 |
|------|------|--------|
| 进行中 `.active` | `#d1fae5` | `#059669` |
| 审核中 `.review` | `#fef3c7` | `#d97706` |
| 草稿 `.draft` | `#f1f5f9` | `#64748b` |

### 7.7 统计图标

| 变体 | 渐变背景 | 图标色 |
|------|---------|--------|
| `.stat-icon.blue` | `#bfdbfe` → `#93c5fd` | `#2563eb` |
| `.stat-icon.purple` | `#ddd6fe` → `#c4b5fd` | `#7c3aed` |
| `.stat-icon.pink` | `#fce7f3` → `#f9a8d4` | `#db2777` |
| `.stat-icon.green` | `#d1fae5` → `#6ee7b7` | `#059669` |

### 7.8 头像

```css
.avatar {
  width: 34px; height: 34px;
  border-radius: 10px;
  background: linear-gradient(135deg, #f472b6, #c084fc);
  color: white; font-weight: 600; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
}
```

### 7.9 色板卡片

```css
.palette-swatch {
  flex: 1; height: 48px;
  border-radius: 12px;
  transition: all 0.3s cubic-bezier(0.16,1,0.3,1);
}
.palette-swatch:hover { transform: scale(1.05); }
.palette-swatch::after {
  /* hover 时显示颜色名称标签 */
  opacity: 0 → 1;
}
```

---

## 8. 动效规范

### 8.1 缓动曲线

| 名称 | 值 | 用途 |
|------|-----|------|
| Smooth | `cubic-bezier(0.16, 1, 0.3, 1)` | 所有 UI 过渡、入场动画 |

### 8.2 入场动画

```css
@keyframes slideUp {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}

.animate-in {
  animation: slideUp 0.6s cubic-bezier(0.16,1,0.3,1) both;
}
```

**错开延迟表：**

| Class | 延迟 | 适用组件 |
|-------|------|---------|
| `.delay-1` | 0.05s | 侧边栏 |
| `.delay-2` | 0.10s | 顶栏、问候语 |
| `.delay-3` | 0.15s | 统计卡片 1–2 |
| `.delay-4` | 0.20s | 统计卡片 3–4 |
| `.delay-5` | 0.25s | 项目列表、任务面板 |
| `.delay-6` | 0.30s | 动态面板、色板 |

### 8.3 过渡时长

| 交互 | 时长 | 缓动 |
|------|------|------|
| 导航项 hover | 250ms | Smooth |
| 输入框 focus | 250ms | Smooth |
| 卡片 hover 上浮 | 350ms | Smooth |
| 按钮 hover | 250ms | Smooth |
| 复选框切换 | 200ms | Smooth |
| 色板 hover 放大 | 300ms | Smooth |

### 8.4 微交互细节

- **统计卡片 hover**：`translateY(-2px)` + 阴影加深 + 渐变遮罩渐显
- **导航项 active 指示条**：3px 宽紫粉渐变竖条，固定左侧
- **通知红点**：`#f472b6` 实心圆，`border: 2px solid white` 分层
- **任务复选框**：点击切换 `done` class，文字同时划线变灰

---

## 9. 可访问性

### 9.1 色彩对比度

| 元素 | 前景 | 背景 | 对比度 | 达标 |
|------|------|------|--------|------|
| 正文 | `#1e293b` | `rgba(255,255,255,0.55)` | ~6.5:1 | AA |
| 辅助文字 | `#64748b` | 同上 | ~4.6:1 | AA |
| 占位符 | `#94a3b8` | `rgba(255,255,255,0.6)` | ~3.2:1 | — |
| Badge 文字 | `#fff` | 渐变紫 | ~4.8:1 | AA |

### 9.2 键盘导航

- 所有 `.task-checkbox` 设置 `tabindex="0"` + `role="checkbox"`
- Enter / Space 触发切换
- 导航项和按钮天然可聚焦

### 9.3 动效降级

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

### 9.4 触摸目标

- 图标按钮：38 × 38px（> 44px 建议值接近）
- 导航项：padding 10px 12px，有效点击区域充足
- 复选框：20 × 20px

---

## 10. 技术规格

### 10.1 技术栈

| 层级 | 选型 |
|------|------|
| 结构 | 纯 HTML5（单文件） |
| 样式 | CSS3（自定义属性 + backdrop-filter + Grid + Flexbox） |
| 动效 | Canvas 2D API + requestAnimationFrame |
| 字体 | Google Fonts CDN（Space Grotesk） |
| 图标 | 内联 SVG（Feather Icons 风格） |

### 10.2 性能指标

| 指标 | 目标 |
|------|------|
| 首帧渲染 | < 100ms |
| 动画帧率 | 稳定 60fps |
| Canvas 分辨率 | 自适应 window.innerWidth/Height |
| Blob 绘制 | 64 段多边形 + 渐变填充 |
| 涟漪叠加 | 每帧最多遍历 ~30 个活跃涟漪 |

### 10.3 浏览器兼容

| 特性 | Chrome | Firefox | Safari | Edge |
|------|--------|---------|--------|------|
| `backdrop-filter` | 76+ | 103+ | 18+ (WebKit) | 79+ |
| CSS Custom Properties | 全支持 | 全支持 | 全支持 | 全支持 |
| Canvas 2D | 全支持 | 全支持 | 全支持 | 全支持 |
| CSS Grid | 全支持 | 全支持 | 全支持 | 全支持 |

---

## 11. 文件结构

```
project/
├── index.html                    # 主入口（单文件包含所有 CSS + JS）
│
├── assets/                       # 预留资源目录（生产环境）
│   ├── images/
│   ├── videos/
│   └── audio/
│
└── FlowSpace-Design-System.md    # 本文档
```

> 当前 Demo 阶段为单文件部署。生产化时建议拆分为 `css/styles.css`、`js/animations.js`、`js/blobs.js`、`js/ripples.js`。

---

## 12. 附录：完整 CSS 变量表

```css
:root {
  /* 色彩 */
  --text-primary: #1e293b;
  --text-secondary: #64748b;
  --text-tertiary: #94a3b8;

  /* 磨砂玻璃 */
  --glass-bg: rgba(255, 255, 255, 0.55);
  --glass-border: rgba(255, 255, 255, 0.7);
  --glass-shadow: 0 8px 32px rgba(148, 163, 184, 0.15);

  /* 圆角 */
  --radius-lg: 24px;
  --radius-md: 16px;
  --radius-sm: 12px;

  /* 动效 */
  --transition-smooth: cubic-bezier(0.16, 1, 0.3, 1);
}
```

---

> **设计师注**：本设计系统以"液体"为核心隐喻——数据流动如水，界面通透如冰，交互轻盈如涟漪。所有设计决策服务于一个目标：让用户在使用工具时感受到平静与专注，而非视觉噪音。