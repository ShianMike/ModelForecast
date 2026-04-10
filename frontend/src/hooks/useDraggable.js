import { useState, useRef, useCallback } from "react";

/**
 * Hook that makes a panel draggable via its header.
 * Uses transform: translate() so existing CSS positioning (top/left/right/bottom) stays intact.
 *
 * Returns:
 *  - offset: { x, y } current drag offset
 *  - handleMouseDown: attach to the header element's onMouseDown
 *  - resetPosition: call to snap back to original position
 */
export default function useDraggable() {
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragging = useRef(false);

  const handleMouseDown = useCallback((e) => {
    // Only left button, ignore clicks on buttons/inputs inside header
    if (e.button !== 0) return;
    const tag = e.target.tagName.toLowerCase();
    if (tag === "button" || tag === "input" || tag === "select" || e.target.closest("button")) return;

    e.preventDefault();
    dragging.current = true;
    const startX = e.clientX - offset.x;
    const startY = e.clientY - offset.y;

    const onMove = (ev) => {
      if (!dragging.current) return;
      setOffset({ x: ev.clientX - startX, y: ev.clientY - startY });
    };
    const onUp = () => {
      dragging.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [offset]);

  const resetPosition = useCallback(() => setOffset({ x: 0, y: 0 }), []);

  return { offset, handleMouseDown, resetPosition };
}
