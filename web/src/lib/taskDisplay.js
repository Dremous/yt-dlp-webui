// 任务展示逻辑独立成纯函数，方便用 Node 内置 test runner 覆盖状态文案、
// 颜色和 aria-label；TaskList 只负责渲染。
export function getTaskDisplay(task, meta) {
  const inPostprocess =
    task.status === "downloading" && Boolean(task.stage);
  const streams = Array.isArray(task.streams) ? task.streams : [];
  // 只有真正存在两条轨时才标注当前轨；单流任务保持原来的“下载中”。
  const streamLabel =
    !inPostprocess && streams.length > 1 && task.current_stream
      ? streams.find((stream) => stream.kind === task.current_stream)?.label || null
      : null;
  return {
    inPostprocess,
    streamLabel,
    label: inPostprocess
      ? task.stage
      : streamLabel
        ? `${meta.label} · ${streamLabel}`
        : meta.label,
    color: inPostprocess ? "warning" : meta.color,
    ariaLabel: inPostprocess
      ? "后期处理进度"
      : streamLabel
        ? `下载进度（${streamLabel}）`
        : "下载进度",
    dotColor: inPostprocess ? "bg-amber-400" : "bg-emerald-400",
  };
}
