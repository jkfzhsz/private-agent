# 示例:React 组件

## 用户请求

请帮我生成一个 React 函数组件 `outputs/FeatureCard.tsx`,展示特性卡片(图标 + 标题 + 描述),使用 Hooks 管理悬停状态,遵循设计系统间距与圆角规范,并提供使用示例。

## 助手响应

我将按 `search_knowledge` → `code_execution` → `file_write` → `file_read` 流程处理。

[search_knowledge] 检索设计系统:
```
query="卡片组件规范 间距 token 圆角", scenario="frontend_design"
```
命中设计 token:卡片间距 `spacing.4=16px`、圆角 `lg=12px`、阴影 `shadow.md`、过渡 `transition.fast=150ms`。

[code_execution] 用 Python 字符串拼接生成 React 组件(函数组件 + Hooks,语义化 props):
```python
from datetime import datetime

now = datetime.now().strftime("%Y-%m-%d %H:%M")

component = f'''/**
 * FeatureCard.tsx - 特性卡片组件
 * 生成时间: {now} | 来源: frontend_design skill
 * 设计 token: spacing.4=16px, radius.lg=12px, shadow.md, transition.fast=150ms
 */
import {{ useState, ReactNode }} from "react";

interface FeatureCardProps {{
  /** 图标节点 */
  icon: ReactNode;
  /** 卡片标题 */
  title: string;
  /** 卡片描述 */
  description: string;
  /** 点击回调(可选) */
  onClick?: () => void;
}}

export function FeatureCard({{
  icon,
  title,
  description,
  onClick,
}}: FeatureCardProps) {{
  const [isHovered, setIsHovered] = useState(false);

  const cardStyle: React.CSSProperties = {{
    display: "flex",
    flexDirection: "column",
    gap: "16px", /* spacing.4 */
    padding: "16px", /* spacing.4 */
    borderRadius: "12px", /* radius.lg */
    border: "1px solid #E5E7EB",
    boxShadow: isHovered
      ? "0 4px 12px rgba(0,0,0,0.08)" /* shadow.md */
      : "0 1px 3px rgba(0,0,0,0.04)",
    transition: "box-shadow 150ms ease", /* transition.fast */
    cursor: onClick ? "pointer" : "default",
  }};

  return (
    <div
      style={{cardStyle}}
      onMouseEnter={{() => setIsHovered(true)}}
      onMouseLeave={{() => setIsHovered(false)}}
      onClick={{onClick}}
    >
      <div aria-hidden="true">{{icon}}</div>
      <h3>{{title}}</h3>
      <p>{{description}}</p>
    </div>
  );
}}

// 使用示例
// import {{ FeatureCard }} from "./FeatureCard";
//
// function App() {{
//   return (
//     <FeatureCard
//       icon={{<span>🚀</span>}}
//       title="快速"
//       description="毫秒级响应"
//       onClick={{() => console.log("clicked")}}
//     />
//   );
// }}
'''

with open("outputs/FeatureCard.tsx", "w", encoding="utf-8") as f:
    f.write(component)
```

[file_write] 输出 `outputs/FeatureCard.tsx`。

[file_read] 读取确认:文件 1 个,包含组件定义 + 使用示例注释。

## 组件结构说明

- `FeatureCardProps`:语义化 props(icon / title / description / onClick)
- `useState`:管理 `isHovered` 状态,驱动阴影过渡
- 样式:内联 style 引用设计 token(间距 16px、圆角 12px、过渡 150ms)
- 交互:鼠标悬停切换 `shadow.md`,点击触发可选回调
- 无障碍:`aria-hidden` 标记装饰性图标

## 设计规范引用

- 间距:`spacing.4=16px`(来源:设计系统知识库)
- 圆角:`radius.lg=12px`(来源:设计系统知识库)
- 阴影:`shadow.md`(来源:设计系统知识库)
- 过渡:`transition.fast=150ms`(来源:设计系统知识库)

## 使用方式

```tsx
import { FeatureCard } from "./FeatureCard";

function App() {
  return (
    <FeatureCard
      icon={<span>🚀</span>}
      title="快速"
      description="毫秒级响应"
      onClick={() => console.log("clicked")}
    />
  );
}
```

文件路径:`outputs/FeatureCard.tsx`
生成时间:[datetime] 2026-08-01
