import React, { useRef, useState } from "react";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  Spinner,
} from "@heroui/react";
import { api } from "../api.js";

export default function CookiePanel({ active, onChanged }) {
  const inputRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  function pickFile() {
    if (inputRef.current) inputRef.current.click();
  }

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    setMsg("");
    try {
      await api.uploadCookie(file);
      onChanged(true);
      setMsg("已导入，将用于之后的下载请求。");
    } catch (err) {
      setMsg("导入失败：" + err.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleClear() {
    if (!window.confirm("确定清除已导入的 Cookie？")) return;
    setBusy(true);
    setMsg("");
    try {
      await api.deleteCookie();
      onChanged(false);
      setMsg("已清除 Cookie。");
    } catch {
      setMsg("清除失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="bg-[#161b2d] border border-[#29324d]">
      <CardHeader className="px-5 pt-5 flex items-center justify-between">
        <h2 className="text-base font-semibold text-white">登录 Cookie</h2>
        <Chip
          size="sm"
          variant="flat"
          color={active ? "success" : "default"}
        >
          {active ? "已导入" : "未导入"}
        </Chip>
      </CardHeader>
      <CardBody className="px-5 pb-5">
        <input
          ref={inputRef}
          type="file"
          accept=".txt,.json,.cookies"
          className="hidden"
          onChange={handleUpload}
        />
        <div className="flex flex-wrap items-center gap-3">
          <Button
            size="sm"
            variant="bordered"
            color="primary"
            onPress={pickFile}
            isDisabled={uploading || busy}
            startContent={
              uploading ? (
                <Spinner size="sm" color="current" />
              ) : (
                <span>↑</span>
              )
            }
          >
            {uploading ? "导入中" : "导入 cookies.txt"}
          </Button>
          {active && (
            <Button
              size="sm"
              variant="light"
              color="danger"
              onPress={handleClear}
              isDisabled={uploading || busy}
            >
              清除
            </Button>
          )}
          <span className="text-xs text-slate-500">
            支持 Netscape 格式 cookies.txt（浏览器插件导出）
          </span>
        </div>
        {msg && (
          <p className={`text-sm mt-3 ${msg.startsWith("导入失败") || msg.startsWith("清除失败") ? "text-red-400" : "text-emerald-400"}`}>
            {msg}
          </p>
        )}
      </CardBody>
    </Card>
  );
}
