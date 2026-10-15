import React from "react";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  Progress,
} from "@heroui/react";

const STATE_META = {
  queued: { label: "排队中", color: "default" },
  downloading: { label: "下载中", color: "primary" },
  finished: { label: "已完成", color: "success" },
  failed: { label: "失败", color: "danger" },
  cancelled: { label: "已取消", color: "default" },
};

export default function TaskList({ tasks, onChanged }) {
  const shown = tasks.slice(0, 30);

  return (
    <Card className="bg-[#161b2d] border border-[#29324d]">
      <CardHeader className="px-5 pt-5 flex items-center justify-between">
        <h2 className="text-base font-semibold text-white">下载任务</h2>
        <Chip size="sm" variant="flat" color="primary">
          {tasks.length}
        </Chip>
      </CardHeader>
      <CardBody className="px-5 pb-5">
        {shown.length === 0 ? (
          <p className="text-sm text-slate-500 text-center py-4">暂无任务</p>
        ) : (
          <div className="flex flex-col gap-3">
            {shown.map((t) => (
              <TaskItem key={t.id} task={t} onChanged={onChanged} />
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function TaskItem({ task, onChanged }) {
  const meta = STATE_META[task.status] || { label: task.status, color: "default" };
  const title = task.title ? ` · ${task.title}` : "";

  async function handleCancel() {
    try {
      await api.cancelTask(task.id);
      onChanged();
    } catch (e) {
      window.alert("取消失败：" + e.message);
    }
  }

  async function handleRetry() {
    try {
      await api.download(task.url, task.format);
      onChanged();
    } catch (e) {
      window.alert("重试失败：" + e.message);
    }
  }

  const canRetry = task.status === "failed" || task.status === "cancelled";

  return (
    <div className="p-4 rounded-xl bg-[#0d1322] border border-[#29324d] flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <span
          className="text-sm text-slate-300 truncate flex-1"
          title={task.url}
        >
          {task.url}
        </span>
        <Chip size="sm" variant="flat" color={meta.color}>
          {meta.label}
        </Chip>
      </div>

      <Progress
        aria-label="下载进度"
        size="sm"
        value={task.progress}
        className="max-w-full"
        color={task.status === "failed" ? "danger" : "primary"}
      />

      <div className="flex items-center gap-4 text-xs text-slate-400">
        {task.status === "downloading" && (
          <>
            <span>{Math.round(task.progress)}%</span>
            {task.speed && <span>{task.speed}</span>}
            {task.eta && <span>剩余 {task.eta}</span>}
          </>
        )}
        <span className="flex-1 truncate text-slate-300">{title}</span>
        {task.status === "downloading" && (
          <Button size="sm" color="warning" variant="light" onPress={handleCancel}>
            取消
          </Button>
        )}
        {canRetry && (
          <Button size="sm" color="primary" variant="flat" onPress={handleRetry}>
            重试
          </Button>
        )}
        {task.status === "finished" && task.filename && (
          <a href={`/files/${encodeURIComponent(task.filename)}`}>
            <Button size="sm" color="success" variant="flat">
              下载文件
            </Button>
          </a>
        )}
      </div>

      {task.error && (
        <p className="text-xs text-red-400 break-all">{task.error}</p>
      )}
    </div>
  );
}
