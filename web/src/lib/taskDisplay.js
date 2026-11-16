// 任务展示逻辑独立成纯函数，方便用 Node 内置 test runner 覆盖状态文案、
// 颜色和 aria-label；TaskList 只负责渲染。
export function getTaskDisplay(task, meta) {
  const inPostprocess =
    task.status === "downloading" && Boolean(task.stage);
  return {
    inPostprocess,
    label: inPostprocess ? task.stage : meta.label,
    color: inPostprocess ? "warning" : meta.color,
    ariaLabel: inPostprocess ? "后期处理进度" : "下载进度",
    dotColor: inPostprocess ? "bg-amber-400" : "bg-emerald-400",
  };
}
