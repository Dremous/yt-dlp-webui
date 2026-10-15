import { useReducedMotion } from "framer-motion";

// 统一缓动曲线（近似 easeOutExpo，收尾柔顺）
export const EASE = [0.22, 1, 0.36, 1];

/**
 * 是否允许播放动效。跟随系统"减弱动效"设置，
 * 为 true 时组件应跳过非必要的入场/退出动画，直接呈现最终状态。
 */
export function useMotionSafe() {
  return !useReducedMotion();
}

// 列表容器：子项依次入场
export const listContainer = {
  hidden: {},
  show: { transition: { staggerChildren: 0.055, delayChildren: 0.02 } },
};

// 卡片/区块入场：轻微上浮 + 淡入
export const cardItem = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.4, ease: EASE } },
};

// 任务项：淡入 + 微缩放
export const taskItem = {
  hidden: { opacity: 0, y: 8, scale: 0.985 },
  show: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.28, ease: EASE } },
  exit: { opacity: 0, x: -14, transition: { duration: 0.2, ease: "easeIn" } },
};

// 文件行：删除前淡出
export const rowItem = {
  hidden: { opacity: 0, y: 6 },
  show: { opacity: 1, y: 0, transition: { duration: 0.26, ease: EASE } },
  exit: { opacity: 0, height: 0, transition: { duration: 0.2, ease: "easeIn" } },
};
