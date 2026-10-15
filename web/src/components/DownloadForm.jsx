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
} from "@heroui/react";
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
      onSubmit();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="card-ui">
      <CardHeader className="px-5 pt-5">
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
            >
              {formats.map((f) => (
                <SelectItem key={f.id}>{f.label}</SelectItem>
              ))}
            </Select>
            <Button
              type="submit"
              isLoading={loading}
              spinner={<Spinner size="sm" color="current" />}
              className="sm:self-end font-semibold text-white bg-indigo-600 hover:bg-indigo-500 shadow-lg shadow-indigo-600/30 transition-ui"
            >
              {loading ? "提交中" : "下载"}
            </Button>
          </div>

          {error && (
            <p className="text-sm text-red-400 break-all">{error}</p>
          )}
          <p className="text-xs text-slate-500">
            单个 URL · 后台并发下载 · 完成后自动出现在文件列表
          </p>
        </form>
      </CardBody>
    </Card>
  );
}
