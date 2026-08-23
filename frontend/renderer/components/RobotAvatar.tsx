// 私人智能体头像 —— 直接使用桌面图标原图(build/icon-256.png 复制的 robot-icon.png)
// 2026-08-08 修复: 用户反馈手绘 SVG 与桌面图标"完全不同, 太丑" → 改用原图,
// 保证任意位置的头像与桌面快捷方式图标完全一致。
import robotIcon from "../assets/robot-icon.png";

interface Props {
  size?: number;
  className?: string;
  style?: React.CSSProperties;
}

export default function RobotAvatar({ size = 40, className, style }: Props): JSX.Element {
  return (
    <img
      src={robotIcon}
      width={size}
      height={size}
      className={className}
      style={{
        display: "block",
        flexShrink: 0,
        borderRadius: 10,
        objectFit: "cover",
        userSelect: "none",
        ...style,
      }}
      alt="私人智能体头像"
      draggable={false}
    />
  );
}