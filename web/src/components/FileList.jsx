import React, { useState } from "react";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  addToast,
  useDisclosure,
} from "@heroui/react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowClockwise,
  DownloadSimple,
  File as FileIcon,
  FileAudio,
  FileImage,
  FileVideo,
  FolderOpen,
  Trash,
} from "@phosphor-icons/react";
import { api } from "../api.js";
import EmptyState from "./EmptyState.jsx";
import { rowItem, useMotionSafe } from "../lib/motion.js";

// 依据扩展名选择文件图标与配色
function fileMeta(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["mp4", "mkv", "webm", "mov", "avi", "flv"].includes(ext))
    return { Icon: FileVideo, color: "text-sky-400" };
  if (["mp3", "m4a", "opus", "wav", "flac", "aac", "ogg"].includes(ext))
    return { Icon: FileAudio, color: "text-violet-400" };
  if (["jpg", "jpeg", "png", "webp", "gif", "avif"].includes(ext))
    return { Icon: FileImage, color: "text-amber-400" };
  return { Icon: FileIcon, color: "text-slate-400" };
}

function formatSize(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = Math.floor(Math.log(bytes) / Math.log(1024));
  i = Math.min(i, units.length - 1);
  return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + units[i];
}

function formatTime(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

export default function FileList({ files, onChanged }) {
  const [pending, setPending] = useState(null);
  const { isOpen, onOpen, onOpenChange } = useDisclosure();

  function askDelete(name) {
    setPending(name);
    onOpen();
  }

  async function confirmDelete() {
    if (!pending) return;
    try {
      await api.deleteFile(pending);
      addToast({ title: "已删除", description: pending, color: "success" });
      onChanged();
    } catch (e) {
      addToast({ title: "删除失败", description: e.message, color: "danger" });
    } finally {
      setPending(null);
    }
  }

  return (
    <Card>
      <CardHeader className="px-5 pt-5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FolderOpen size={18} weight="bold" className="text-emerald-400" aria-hidden="true" />
          <h2 className="text-base font-semibold text-white">已下载文件</h2>
        </div>
        <Button
          size="sm"
          disableRipple
          variant="flat"
          color="default"
          onPress={onChanged}
          className="transition-ui"
          startContent={<ArrowClockwise size={15} aria-hidden="true" />}
        >
          刷新
        </Button>
      </CardHeader>
      <CardBody className="px-5 pb-5">
        {files.length === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title="暂无文件"
            hint="下载完成后文件会在这里出现"
          />
        ) : (
          <div className="flex flex-col">
            {/* 桌面表头 */}
            <div className="hidden sm:flex items-center gap-3 px-3 pb-2 text-xs text-slate-500">
              <span className="flex-1">文件名</span>
              <span className="w-20 text-right">大小</span>
              <span className="w-40 text-right">下载时间</span>
              <span className="w-24 text-right">操作</span>
            </div>
            <AnimatePresence initial={false}>
              {files.map((f) => (
                <FileRow
                  key={f.name}
                  file={f}
                  onDelete={() => askDelete(f.name)}
                />
              ))}
            </AnimatePresence>
          </div>
        )}

        {/* 删除二次确认：替换原生 window.confirm */}
        <Modal isOpen={isOpen} onOpenChange={onOpenChange} backdrop="blur">
          <ModalContent>
            {(onClose) => (
              <>
                <ModalHeader className="text-white">删除文件</ModalHeader>
                <ModalBody>
                  <p className="text-sm text-slate-300 break-all">
                    确定删除「{pending}」？此操作不可恢复。
                  </p>
                </ModalBody>
                <ModalFooter>
                  <Button variant="light" onPress={onClose}>
                    取消
                  </Button>
                  <Button
                    color="danger"
                    onPress={() => {
                      onClose();
                      confirmDelete();
                    }}
                  >
                    删除
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

function FileRow({ file, onDelete }) {
  const motionSafe = useMotionSafe();
  const { Icon, color } = fileMeta(file.name);

  return (
    <motion.div
      layout={motionSafe}
      variants={rowItem}
      initial={motionSafe ? "hidden" : false}
      animate="show"
      exit={motionSafe ? "exit" : undefined}
      className="flex items-center gap-3 px-3 py-2.5 rounded-xl border border-transparent hover:bg-white/[0.03] hover:border-[#26304a] transition-ui overflow-hidden"
    >
      <Icon size={20} weight="duotone" className={`shrink-0 ${color}`} aria-hidden="true" />
      <div className="flex-1 min-w-0">
        <span className="block truncate text-sm text-slate-200">{file.name}</span>
        <span className="sm:hidden text-xs text-slate-500">
          {formatSize(file.size)} · {formatTime(file.mtime)}
        </span>
      </div>
      <span className="hidden sm:block w-20 text-right text-xs text-slate-400 tabular-nums">
        {formatSize(file.size)}
      </span>
      <span className="hidden sm:block w-40 text-right text-xs text-slate-400 tabular-nums">
        {formatTime(file.mtime)}
      </span>
      <div className="w-auto sm:w-24 flex justify-end gap-1.5">
        <Button
          as="a"
          href={`/files/${encodeURIComponent(file.name)}`}
          isIconOnly
          size="sm"
          disableRipple
          color="success"
          variant="flat"
          className="transition-ui"
          aria-label={`下载 ${file.name}`}
        >
          <DownloadSimple size={15} weight="bold" aria-hidden="true" />
        </Button>
        <Button
          isIconOnly
          size="sm"
          disableRipple
          color="danger"
          variant="light"
          onPress={onDelete}
          className="transition-ui"
          aria-label={`删除 ${file.name}`}
        >
          <Trash size={15} weight="bold" aria-hidden="true" />
        </Button>
      </div>
    </motion.div>
  );
}
