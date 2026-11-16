import React, { useState } from "react";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Progress,
  addToast,
  useDisclosure,
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
  Trash,
  X,
  XCircle,
} from "@phosphor-icons/react";
import { api } from "../api.js";
import EmptyState from "./EmptyState.jsx";
import { getTaskDisplay } from "../lib/taskDisplay.js";
import { taskItem, useMotionSafe } from "../lib/motion.js";

const STATE_META = {
  queued: { label: "排队中", color: "default", Icon: Clock },
  downloading: { label: "下载中", color: "primary", Icon: DownloadSimple },
  finished: { label: "已完成", color: "success", Icon: CheckCircle },
  failed: { label: "失败", color: "danger", Icon: XCircle },
  cancelled: { label: "已取消", color: "default", Icon: Prohibit },
};

// 只有已结束的任务允许删除：进行中的必须先取消，避免和后台线程争抢同一目录
const ENDED_STATES = ["finished", "failed", "cancelled"];

// 确认弹窗的文案随状态变化：删已完成任务不会动磁盘上的视频文件
function pendingMessage(pending) {
  if (!pending) return "";
  if (pending.kind === "all") {
    return "将移除所有已结束的任务记录。失败/取消的任务会连带清理临时文件与日志；已完成任务的视频文件会保留在磁盘上。";
  }
  if (pending.task.status === "finished") {
    return "仅移除任务记录，已下载的文件会保留在磁盘上。";
  }
  return "将移除任务记录，并清理该任务的临时文件与日志。";
}

export default function TaskList({ tasks, onChanged, total, ended = 0 }) {
  const shown = tasks.slice(0, 30);
  // 待删除目标：{ kind: "one", task } 或 { kind: "all" }；null 表示弹窗空闲
  const [pending, setPending] = useState(null);
  const { isOpen, onOpen, onOpenChange } = useDisclosure();

  function askDelete(task) {
    setPending({ kind: "one", task });
    onOpen();
  }

  function askCleanup() {
    setPending({ kind: "all" });
    onOpen();
  }

  // target 显式传入：点确认时 Modal 会先关闭并清空 pending，
  // 依赖闭包读 pending 虽然也能工作，但显式传参更不容易被后续重构改坏。
  async function confirmPending(target = pending) {
    if (!target) return;
    try {
      if (target.kind === "all") {
        const data = await api.cleanupTasks();
        addToast({
          title: "已清理",
          description: `移除 ${data.removed} 条已结束任务`,
          color: "success",
        });
      } else {
        const data = await api.removeTask(target.task.id);
        addToast({
          title: "已删除",
          description: data.removed_files
            ? "任务记录与临时文件已清理"
            : "任务记录已移除，文件已保留",
          color: "success",
        });
      }
      onChanged();
    } catch (e) {
      addToast({ title: "操作失败", description: e.message, color: "danger" });
    } finally {
      setPending(null);
    }
  }

  return (
    <Card>
      <CardHeader className="px-5 pt-5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ListChecks size={18} weight="bold" className="text-emerald-400" aria-hidden="true" />
          <h2 className="text-base font-semibold text-white">下载任务</h2>
        </div>
        <div className="flex items-center gap-2">
          {ended > 0 && (
            <Button
              size="sm"
              disableRipple
              variant="light"
              color="danger"
              onPress={askCleanup}
              className="transition-ui"
              title="清理全部已结束任务（包括列表之外的）"
              startContent={<Trash size={14} weight="bold" aria-hidden="true" />}
            >
              清理已结束
            </Button>
          )}
          <Chip size="sm" variant="flat" color="primary">
            {total}
          </Chip>
        </div>
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
                  <TaskItem
                    key={t.id}
                    task={t}
                    onChanged={onChanged}
                    onDelete={() => askDelete(t)}
                  />
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

        {/* 单个删除与批量清理共用一个 Modal，避免每行各建一个实例 */}
        <Modal
          isOpen={isOpen}
          onOpenChange={(open) => {
            onOpenChange(open);
            if (!open) setPending(null);
          }}
          backdrop="blur"
        >
          <ModalContent>
            {(onClose) => (
              <>
                <ModalHeader className="text-white">
                  {pending?.kind === "all" ? "清理已结束任务" : "删除任务"}
                </ModalHeader>
                <ModalBody>
                  <p className="text-sm text-slate-300 break-words">
                    {pendingMessage(pending)}
                  </p>
                </ModalBody>
                <ModalFooter>
                  <Button variant="light" onPress={onClose}>
                    取消
                  </Button>
                  <Button
                    color="danger"
                    onPress={() => {
                      const target = pending;
                      onClose();
                      confirmPending(target);
                    }}
                  >
                    确认删除
                  </Button>
                </ModalFooter>
              </>
            )}
          </ModalContent>
        </Modal>
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

function TaskItem({ task, onChanged, onDelete }) {
  const meta = STATE_META[task.status] || {
    label: task.status,
    color: "default",
    Icon: DownloadSimple,
  };
  // 合并 / 转码属于 postprocessing：status 仍是 downloading，但继续显示
  // “下载中”会让用户以为还在拉字节。展示逻辑抽到纯函数，便于单测覆盖。
  const display = getTaskDisplay(task, meta);
  const {
    inPostprocess,
    label: displayLabel,
    color: displayColor,
    ariaLabel,
    dotColor,
  } = display;
  const motionSafe = useMotionSafe();
  const title = task.title ? ` · ${task.title}` : "";
  const isEnded = ENDED_STATES.includes(task.status);
  // 两轨各自的状态只在下载阶段显示；合并/转码阶段让位给 stage，避免和
  // “合并中”叠在一起。单流任务（streams.length <= 1）保持原有外观。
  const tracks =
    task.status === "downloading" &&
    !task.stage &&
    Array.isArray(task.streams) &&
    task.streams.length > 1
      ? task.streams
      : [];

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

  // 排队任务也在等待并发位，必须能从界面取消；后端接口本来就支持 queued。
  const canCancel = task.status === "queued" || task.status === "downloading";
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
          color={displayColor}
          startContent={
            task.status === "downloading" ? (
              <span className="relative flex w-2 h-2" aria-hidden="true">
                <span className={`absolute inline-flex w-full h-full rounded-full ${dotColor} animate-ping-soft`} />
                <span className={`relative inline-flex w-2 h-2 rounded-full ${dotColor}`} />
              </span>
            ) : (
              <Icon size={13} weight="bold" aria-hidden="true" />
            )
          }
        >
          {displayLabel}
        </Chip>
      </div>

      <div className="relative">
        <Progress
          aria-label={ariaLabel}
          size="sm"
          value={task.progress}
          className="max-w-full"
          color={inPostprocess ? "warning" : progressColor(task.status)}
        />
        {/* 合并/转码阶段没有字节流量，流光会让「已经下完」看起来还在动，这里停掉 */}
        {task.status === "downloading" && !task.stage && (
          <span
            className="pointer-events-none absolute inset-0 overflow-hidden rounded-full"
            aria-hidden="true"
          >
            <span className="absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-white/30 to-transparent animate-shimmer" />
          </span>
        )}
      </div>

      {tracks.length > 0 && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {tracks.map((track) => {
            const done = track.status === "finished";
            const value = done ? 100 : Number(track.progress) || 0;
            return (
              <div key={track.kind} className="flex flex-col gap-1">
                <div className="flex items-center justify-between gap-2 text-[11px] text-slate-400">
                  <span>{track.label}</span>
                  <span className={done ? "text-emerald-400" : "text-slate-300"}>
                    {done ? "已完成" : `${Math.round(value)}%`}
                  </span>
                </div>
                <Progress
                  aria-label={`${track.label}进度`}
                  size="sm"
                  value={value}
                  color={done ? "success" : track.status === "downloading" ? "primary" : "default"}
                  className="max-w-full"
                />
              </div>
            );
          })}
        </div>
      )}

      <div className="flex items-center gap-4 text-xs text-slate-400">
        {task.status === "downloading" && (
          <>
            <span className="tabular-nums text-slate-300">
              {Math.round(task.progress)}%
            </span>
            {!task.stage && task.speed && <span>{task.speed}</span>}
            {!task.stage && task.eta && <span>剩余 {task.eta}</span>}
          </>
        )}
        <span className="flex-1 truncate text-slate-300">{title}</span>
        {canCancel && (
          <Button
            size="sm"
            disableRipple
            color="warning"
            variant="light"
            onPress={handleCancel}
            className="transition-ui"
            startContent={<X size={14} weight="bold" aria-hidden="true" />}
          >
            {task.status === "queued" ? "取消排队" : "取消"}
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
        {isEnded && (
          <Button
            isIconOnly
            size="sm"
            disableRipple
            color="danger"
            variant="light"
            onPress={onDelete}
            className="transition-ui"
            aria-label="删除任务"
          >
            <Trash size={14} weight="bold" aria-hidden="true" />
          </Button>
        )}
      </div>

      {task.error && (
        <p className="text-xs text-red-400 break-all">{task.error}</p>
      )}
    </motion.div>
  );
}
