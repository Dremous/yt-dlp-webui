import React from "react";
import { motion } from "framer-motion";
import { useMotionSafe } from "../lib/motion.js";

/**
 * 通用空状态：图标 + 主文案 + 辅助提示。
 * 图标为装饰性（aria-hidden），文案提供可读信息。
 */
export default function EmptyState({ icon: Icon, title, hint }) {
  const motionSafe = useMotionSafe();

  return (
    <motion.div
      initial={motionSafe ? { opacity: 0, y: 8 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
      className="flex flex-col items-center justify-center gap-2 py-8 text-center"
    >
      {Icon ? (
        <span className="flex items-center justify-center w-14 h-14 rounded-2xl bg-white/[0.03] border border-white/5 text-emerald-400/70">
          <Icon size={26} weight="duotone" aria-hidden="true" />
        </span>
      ) : null}
      <p className="text-sm text-slate-400">{title}</p>
      {hint ? <p className="text-xs text-slate-600">{hint}</p> : null}
    </motion.div>
  );
}
