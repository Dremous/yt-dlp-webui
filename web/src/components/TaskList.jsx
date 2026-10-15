import React from "react";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  Progress,
  addToast,
} from "@heroui/react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowClockwise,
  CheckCircle,
  Clock,
  DownloadSimple,
  FileArrowDown,
  ListChecks,
  Prohibit,
  X,
  XCircle,
} from "@phosphor-icons/react";
import { api } from "../api.js";
import EmptyState from "./EmptyState.jsx";
import { taskItem, useMotionSafe } from "../lib/motion.js";

const STATE_META = {
  queued: { label: "排队中", color: "default", Icon: Clock },
  downloading: { label: "下载中", color: "primary", Icon: DownloadSimple },
  finished: { label: "已完成", color: "success", Icon: CheckCircle },
  failed: { label: "失败", color: "danger", Icon: XCircle },
  cancelled: { label: "已取消", color: "default", Icon: Prohibit },
};

export default function TaskList({ tasks, onChanged, total }) {
  const shown = tasks.slice(0, 30);

  return (
    <Card className="card-ui">
      <CardHeader className="px-5 pt-5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ListChecks size={18} weight="bold" className="text-emerald-400" aria-hidden="true" />
          <h2 className="text-base font-semibold text-white">下载任务</h2>
        </div>
        <Chip size="sm" variant="flat" color="primary">
          {total}
        </Chip>
      </CardHeader>
      <CardBody className="px-5 pb-5">
        {shown.length === 0 ? (
          <EmptyState
            icon={ListChecks}
            title="暂无任务"
            hint="在上方粘贴链接开始第一个下载"
          />
        ) : (
          <>
            <div className="flex flex-col gap-3">
              <AnimatePresence initial={false} mode="popLayout">
                {shown.map((t) => (
                  <TaskItem key={t.id} task={t} onChanged={onChanged} />
                ))}
              </AnimatePresence>
            </div>
            {total > shown.length && (
              <p className="text-xs text-slate-500 mt-3">
                仅显示最近 {shown.length} 条（共 {total}）
              </p>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

function progressColor(status) {
  if (status === "finished") return "success";
  if (status === "failed") return "danger";
  if (status === "downloading") return "primary";
  return "default";
}

function TaskItem({ task, onChanged }) {
  const meta = STATE_META[task.status] || { label: task.status, color: "default" };
  const motionSafe = useMotionSafe();
  const title = task.title ? ` · ${task.title}` : "";

  async function handleCancel() {
    try {
      await api.cancelTask(task.id);
      addToast({ title: "已取消", description: "任务已中止", color: "default" });
      onChanged();
    } catch (e) {
      addToast({ title: "取消失败", description: e.message, color: "danger" });
    }
  }

  async function handleRetry() {
    try {
      await api.download(task.url, task.format);
      addToast({ title: "已重新提交", color: "success" });
      onChanged();
    } catch (e) {
      addToast({ title: "重试失败", description: e.message, color: "danger" });
    }
  }

  const canRetry = task.status === "failed" || task.status === "cancelled";
  const { Icon } = meta;

  return (
    <motion.div
      layout={motionSafe}
      variants={taskItem}
      initial={motionSafe ? "hidden" : false}
      animate="show"
      exit={motionSafe ? "exit" : undefined}
      className="p-4 rounded-xl bg-[#131c2e] border border-[#26304a] flex flex-col gap-3 transition-ui hover:border-emerald-500/40"
    >
      <div className="flex items-center justify-between gap-3">
        <span
          className="text-sm text-slate-300 truncate flex-1 font-mono-ui"
          title={task.url}
        >
          {task.url}
        </span>
        <Chip
          size="sm"
          variant="flat"
          color={meta.color}
          startContent={
            task.status === "downloading" ? (
              <span className="relative flex w-2 h-2" aria-hidden="true">
                <span className="absolute inline-flex w-full h-full rounded-full bg-emerald-400 animate-ping-soft" />
                <span className="relative inline-flex w-2 h-2 rounded-full bg-emerald-400" />
              </span>
            ) : (
              <Icon size={13} weight="bold" aria-hidden="true" />
            )
          }
        >
          {meta.label}
        </Chip>
      </div>

      <div className="relative">
        <Progress
          aria-label="下载进度"
          size="sm"
          value={task.progress}
          className="max-w-full"
          color={progressColor(task.status)}
        />
        {task.status === "downloading" && (
          <span
            className="pointer-events-none absolute inset-0 overflow-hidden rounded-full"
            aria-hidden="true"
          >
            <span className="absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-white/30 to-transparent animate-shimmer" />
          </span>
        )}
      </div>

      <div className="flex items-center gap-4 text-xs text-slate-400">
        {task.status === "downloading" && (
          <>
            <span className="tabular-nums text-slate-300">
              {Math.round(task.progress)}%
            </span>
            {task.speed && <span>{task.speed}</span>}
            {task.eta && <span>剩余 {task.eta}</span>}
          </>
        )}
        <span className="flex-1 truncate text-slate-300">{title}</span>
        {task.status === "downloading" && (
          <Button
            size="sm"
            disableRipple
            color="warning"
            variant="light"
            onPress={handleCancel}
            className="transition-ui"
            startContent={<X size={14} weight="bold" aria-hidden="true" />}
          >
            取消
          </Button>
        )}
        {canRetry && (
          <Button
            size="sm"
            disableRipple
            color="primary"
            variant="flat"
            onPress={handleRetry}
            className="transition-ui"
            startContent={<ArrowClockwise size={14} weight="bold" aria-hidden="true" />}
          >
            重试
          </Button>
        )}
        {task.status === "finished" && task.filename && (
          <Button
            as="a"
            href={`/files/${encodeURIComponent(task.filename)}`}
            size="sm"
            disableRipple
            color="success"
            variant="flat"
            className="transition-ui"
            startContent={<FileArrowDown size={14} weight="bold" aria-hidden="true" />}
          >
            下载文件
          </Button>
        )}
      </div>

      {task.error && (
        <p className="text-xs text-red-400 break-all">{task.error}</p>
      )}
    </motion.div>
  );
}
