import React, { useRef, useState } from "react";
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
  Spinner,
  addToast,
  useDisclosure,
} from "@heroui/react";
import { Cookie, Trash, UploadSimple } from "@phosphor-icons/react";
import { api } from "../api.js";

export default function CookiePanel({ active, onChanged }) {
  const inputRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const { isOpen, onOpen, onOpenChange } = useDisclosure();

  function pickFile() {
    if (inputRef.current) inputRef.current.click();
  }

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    try {
      await api.uploadCookie(file);
      onChanged(true);
      addToast({
        title: "Cookie 已导入",
        description: "将用于之后的下载请求",
        color: "success",
      });
    } catch (err) {
      addToast({ title: "导入失败", description: err.message, color: "danger" });
    } finally {
      setUploading(false);
    }
  }

  async function handleClear() {
    setBusy(true);
    try {
      await api.deleteCookie();
      onChanged(false);
      addToast({ title: "Cookie 已清除", color: "success" });
    } catch {
      addToast({ title: "清除失败", description: "请重试", color: "danger" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="card-ui">
      <CardHeader className="px-5 pt-5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cookie size={18} weight="bold" className="text-emerald-400" aria-hidden="true" />
          <h2 className="text-base font-semibold text-white">登录 Cookie</h2>
        </div>
        <Chip
          size="sm"
          variant="flat"
          color={active ? "success" : "default"}
          startContent={
            <span
              className={
                active
                  ? "w-1.5 h-1.5 rounded-full bg-emerald-400"
                  : "w-1.5 h-1.5 rounded-full bg-slate-500"
              }
              aria-hidden="true"
            />
          }
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
            disableRipple
            variant="bordered"
            color="primary"
            onPress={pickFile}
            isDisabled={uploading || busy}
            className="active:scale-[.97] transition-ui"
            startContent={
              uploading ? (
                <Spinner size="sm" color="current" />
              ) : (
                <UploadSimple size={16} weight="bold" aria-hidden="true" />
              )
            }
          >
            {uploading ? "导入中" : "导入 cookies.txt"}
          </Button>
          {active && (
            <Button
              size="sm"
              disableRipple
              variant="light"
              color="danger"
              onPress={onOpen}
              isDisabled={uploading || busy}
              className="transition-ui"
              startContent={<Trash size={15} aria-hidden="true" />}
            >
              清除
            </Button>
          )}
          <span className="text-xs text-slate-500">
            支持 Netscape 格式 cookies.txt（浏览器插件导出）
          </span>
        </div>

        {/* 清除二次确认：替换原生 window.confirm */}
        <Modal isOpen={isOpen} onOpenChange={onOpenChange} backdrop="blur">
          <ModalContent>
            {(onClose) => (
              <>
                <ModalHeader className="text-white">清除 Cookie</ModalHeader>
                <ModalBody>
                  <p className="text-sm text-slate-300">
                    确定清除已导入的 Cookie？之后的下载请求将不再携带登录凭证。
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
                      handleClear();
                    }}
                  >
                    清除
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
