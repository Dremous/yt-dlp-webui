import React, { useEffect, useState } from "react";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Chip,
  Input,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Spinner,
  addToast,
  useDisclosure,
} from "@heroui/react";
import {
  ArrowsClockwise,
  CheckCircle,
  Globe,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react";
import { api } from "../api.js";

export default function ProxyPanel({ active, current, onChanged }) {
  const [input, setInput] = useState(current || "");
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  // 测试/保存的结果提示：{ ok, text, hint }，hint 是「地址填错」类的额外提醒
  const [result, setResult] = useState(null);
  const { isOpen, onOpen, onOpenChange } = useDisclosure();

  // 父组件加载到最新状态后同步到输入框（含页面首次加载）
  useEffect(() => {
    setInput(current || "");
  }, [current]);

  async function handleSave() {
    const value = input.trim();
    if (!value) {
      addToast({ title: "请先填写代理地址", color: "warning" });
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      const data = await api.saveProxy(value);
      setInput(data.url); // 回填规范化后的地址（例如自动补上的 http://）
      onChanged(true, data.url);
      setResult(data.hint ? { ok: null, text: null, hint: data.hint } : null);
      addToast({
        title: "代理已保存",
        description: "之后的下载请求都会走这个代理",
        color: "success",
      });
    } catch (err) {
      addToast({ title: "保存失败", description: err.message, color: "danger" });
    } finally {
      setBusy(false);
    }
  }

  async function handleTest() {
    const value = input.trim();
    setTesting(true);
    setResult(null);
    try {
      // 传地址就直接测这个地址（不必先保存），为空则测已保存的代理
      const data = await api.testProxy(value || undefined);
      setResult({
        ok: !!data.ok,
        text: data.ok ? `连通正常（${data.latency_ms} ms）` : data.error,
        hint: data.hint || null,
      });
    } catch (err) {
      setResult({ ok: false, text: err.message, hint: null });
    } finally {
      setTesting(false);
    }
  }

  async function handleClear() {
    setBusy(true);
    try {
      await api.deleteProxy();
      setInput("");
      setResult(null);
      onChanged(false, "");
      addToast({ title: "代理已清除", description: "已恢复直连", color: "success" });
    } catch {
      addToast({ title: "清除失败", description: "请重试", color: "danger" });
    } finally {
      setBusy(false);
    }
  }

  const disabled = busy || testing;

  return (
    <Card>
      <CardHeader className="px-5 pt-5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Globe size={18} weight="bold" className="text-emerald-400" aria-hidden="true" />
          <h2 className="text-base font-semibold text-white">网络代理</h2>
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
          {active ? "已启用" : "未启用"}
        </Chip>
      </CardHeader>
      <CardBody className="px-5 pb-5 flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <Input
            label="代理地址"
            placeholder="http://host.docker.internal:7890"
            value={input}
            onValueChange={setInput}
            variant="bordered"
            isDisabled={disabled}
            className="flex-1 font-mono-ui"
            description="支持 http / https / socks5 / socks5h，省略协议按 http 处理"
          />
          <Button
            disableRipple
            variant="bordered"
            color="primary"
            onPress={handleSave}
            isDisabled={disabled}
            className="active:scale-[.97] transition-ui"
            startContent={
              busy ? (
                <Spinner size="sm" color="current" />
              ) : (
                <Globe size={16} weight="bold" aria-hidden="true" />
              )
            }
          >
            {busy ? "保存中" : "保存"}
          </Button>
          <Button
            disableRipple
            variant="flat"
            color="default"
            onPress={handleTest}
            isDisabled={disabled}
            className="transition-ui"
            startContent={
              testing ? (
                <Spinner size="sm" color="current" />
              ) : (
                <ArrowsClockwise size={16} weight="bold" aria-hidden="true" />
              )
            }
          >
            {testing ? "测试中" : "测试连通"}
          </Button>
          {active && (
            <Button
              disableRipple
              variant="light"
              color="danger"
              onPress={onOpen}
              isDisabled={disabled}
              className="transition-ui"
              startContent={<Trash size={15} aria-hidden="true" />}
            >
              清除
            </Button>
          )}
        </div>

        {/* 保存/测试结果：hint 用黄色警示，结果用绿/红区分 */}
        {result?.hint && (
          <p className="flex items-start gap-1.5 text-xs leading-relaxed text-amber-400">
            <WarningCircle
              size={14}
              weight="fill"
              className="shrink-0 mt-0.5"
              aria-hidden="true"
            />
            {result.hint}
          </p>
        )}
        {result?.text && (
          <p
            className={`flex items-start gap-1.5 text-sm break-all ${
              result.ok ? "text-emerald-400" : "text-red-400"
            }`}
          >
            {result.ok ? (
              <CheckCircle size={15} weight="fill" className="shrink-0 mt-0.5" aria-hidden="true" />
            ) : (
              <WarningCircle size={15} weight="fill" className="shrink-0 mt-0.5" aria-hidden="true" />
            )}
            {result.text}
          </p>
        )}

        <div className="text-xs text-slate-500 leading-relaxed">
          <p className="text-slate-400">
            国内直连 YouTube 会被阻断，配好代理后下载即可正常进行。按代理所在位置选择填法：
          </p>
          <ul className="mt-1 list-disc pl-4 space-y-0.5">
            <li>代理就在本机（宿主机）上：<code>http://host.docker.internal:7890</code></li>
            <li>代理是另一个容器：填容器名，如 <code>http://clash:7890</code>（需在同一 docker 网络）</li>
            <li>代理在局域网其他机器：填那台机器的 IP，如 <code>http://192.168.1.2:7890</code></li>
          </ul>
          <p className="mt-1 text-amber-500/90">
            别填 127.0.0.1（容器内它指向容器自己）；宿主机上的代理需开启「允许局域网连接」并放行防火墙端口。
          </p>
        </div>

        {/* 清除二次确认：与 Cookie 面板保持一致的交互 */}
        <Modal isOpen={isOpen} onOpenChange={onOpenChange} backdrop="blur">
          <ModalContent>
            {(onClose) => (
              <>
                <ModalHeader className="text-white">清除代理</ModalHeader>
                <ModalBody>
                  <p className="text-sm text-slate-300">
                    确定清除已配置的代理？之后的下载将恢复直连，国内网络下可能再次无法访问 YouTube。
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
