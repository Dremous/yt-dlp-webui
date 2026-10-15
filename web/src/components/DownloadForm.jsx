import React, { useState } from "react";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Input,
  Select,
  SelectItem,
  Spinner,
  addToast,
} from "@heroui/react";
import { AnimatePresence, motion } from "framer-motion";
import { DownloadSimple, LinkSimple, WarningCircle } from "@phosphor-icons/react";
import { api } from "../api.js";

export default function DownloadForm({ formats, onSubmit }) {
  const [url, setUrl] = useState("");
  const [format, setFormat] = useState("mp4");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    setLoading(true);
    setError("");
    try {
      await api.download(trimmed, format);
      setUrl("");
      addToast({
        title: "任务已提交",
        description: "下载已在后台开始",
        color: "success",
      });
      onSubmit();
    } catch (err) {
      setError(err.message);
      addToast({ title: "提交失败", description: err.message, color: "danger" });
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader className="px-5 pt-5 flex items-center gap-2">
        <DownloadSimple
          size={18}
          weight="bold"
          className="text-emerald-400"
          aria-hidden="true"
        />
        <h2 className="text-base font-semibold text-white">下载新内容</h2>
      </CardHeader>
      <CardBody className="px-5 pb-5">
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <Input
              isRequired
              label="链接"
              placeholder="https://www.youtube.com/watch?v=..."
              value={url}
              onValueChange={setUrl}
              className="flex-1 font-mono-ui"
              variant="bordered"
              isDisabled={loading}
              startContent={
                <LinkSimple size={16} className="text-slate-500" aria-hidden="true" />
              }
            />
            <Select
              label="输出格式"
              selectedKeys={[format]}
              onSelectionChange={(keys) => {
                const k = Array.from(keys)[0];
                if (k) setFormat(String(k));
              }}
              className="sm:w-64"
              variant="bordered"
              isDisabled={loading}
              classNames={{
                popoverContent:
                  "bg-[#0f1626] border border-[#2a3450] rounded-xl shadow-2xl shadow-black/50",
                listbox: "bg-transparent text-slate-200",
              }}
            >
              {formats.map((f) => (
                <SelectItem key={f.id}>{f.label}</SelectItem>
              ))}
            </Select>
            <Button
              type="submit"
              disableRipple
              color="primary"
              isLoading={loading}
              spinner={<Spinner size="sm" color="current" />}
              startContent={
                !loading && (
                  <DownloadSimple size={17} weight="bold" aria-hidden="true" />
                )
              }
              className="sm:self-end font-semibold shadow-lg shadow-emerald-500/25 active:scale-[.97] transition-ui"
            >
              {loading ? "提交中" : "下载"}
            </Button>
          </div>

          <AnimatePresence>
            {error && (
              <motion.p
                key="error"
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="flex items-center gap-1.5 text-sm text-red-400 break-all"
              >
                <WarningCircle
                  size={15}
                  weight="fill"
                  className="shrink-0"
                  aria-hidden="true"
                />
                {error}
              </motion.p>
            )}
          </AnimatePresence>

          <p className="text-xs text-slate-500">
            单个 URL · 后台并发下载 · 完成后自动出现在文件列表
          </p>
        </form>
      </CardBody>
    </Card>
  );
}
