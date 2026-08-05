// V1.1 布局优化 - 通用拖拽分隔条(ResizeHandle)
// 三栏布局中用于拖拽调整左右分区宽度:
// - mousedown 开始 → window mousemove 增量回调(onDrag, 父组件负责 clamp)
// - mouseup 结束; 拖拽期间禁用文本选择 + 显示 col-resize 光标
import { useEffect, useRef } from "react";

export default function ResizeHandle({
  onDrag,
  onDragEnd,
}: {
  /** 拖拽增量(px): 每次 mousemove 传入相对上次的位移, 父组件累加并 clamp */
  onDrag: (delta: number) => void;
  onDragEnd?: () => void;
}): JSX.Element {
  const dragging = useRef(false);
  const lastX = useRef(0);

  const handleMouseDown = (e: React.MouseEvent): void => {
    dragging.current = true;
    lastX.current = e.clientX;
    e.preventDefault();
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  useEffect(() => {
    const onMove = (e: MouseEvent): void => {
      if (!dragging.current) return;
      const delta = e.clientX - lastX.current;
      lastX.current = e.clientX;
      onDrag(delta);
    };
    const onUp = (): void => {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      onDragEnd?.();
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [onDrag, onDragEnd]);

  return (
    <div
      onMouseDown={handleMouseDown}
      title="拖拽调整宽度"
      style={{
        width: 6,
        flexShrink: 0,
        cursor: "col-resize",
        position: "relative",
        zIndex: 5,
        background: "transparent",
        transition: "background 0.15s ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "rgba(139,92,246,0.25)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent";
      }}
    />
  );
}
