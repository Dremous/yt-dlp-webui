import React from "react";
import {
  Button,
  Chip,
  Divider,
  Link,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Spinner,
  useDisclosure,
} from "@heroui/react";
import { GithubLogo, Info } from "@phosphor-icons/react";

// 单行信息：左标签右取值
function InfoRow({ label, children }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="text-sm text-slate-400">{label}</span>
      <span className="text-sm text-slate-100 font-medium text-right break-all">
        {children}
      </span>
    </div>
  );
}

export default function AboutButton({ about }) {
  const { isOpen, onOpen, onOpenChange } = useDisclosure();

  return (
    <>
      <Button
        isIconOnly
        size="sm"
        disableRipple
        variant="light"
        aria-label="关于"
        onPress={onOpen}
        className="text-slate-400 hover:text-emerald-400 transition-ui"
      >
        <Info size={20} weight="bold" aria-hidden="true" />
      </Button>

      <Modal isOpen={isOpen} onOpenChange={onOpenChange} backdrop="blur">
        <ModalContent>
          {(onClose) => (
            <>
              <ModalHeader className="flex flex-col gap-1 text-white">
                关于
              </ModalHeader>
              <ModalBody>
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold text-white">
                    {about?.name || "yt-dlp WebUI"}
                  </span>
                  {about?.version && (
                    <Chip size="sm" variant="flat" color="success">
                      {about.version}
                    </Chip>
                  )}
                </div>

                <Divider className="my-1 bg-[#26304a]" />

                {about ? (
                  <div className="flex flex-col">
                    <InfoRow label="版本">{about.version}</InfoRow>
                    <InfoRow label="yt-dlp 版本">{about.yt_dlp_version}</InfoRow>
                    <InfoRow label="作者">{about.author}</InfoRow>
                  </div>
                ) : (
                  <div className="flex items-center justify-center py-4">
                    <Spinner size="sm" color="success" />
                  </div>
                )}
              </ModalBody>
              <ModalFooter>
                {about?.repo && (
                  <Button
                    as={Link}
                    href={about.repo}
                    target="_blank"
                    rel="noreferrer"
                    size="sm"
                    variant="bordered"
                    color="primary"
                    className="transition-ui"
                    startContent={
                      <GithubLogo size={16} weight="bold" aria-hidden="true" />
                    }
                  >
                    GitHub
                  </Button>
                )}
                <Button variant="light" onPress={onClose}>
                  关闭
                </Button>
              </ModalFooter>
            </>
          )}
        </ModalContent>
      </Modal>
    </>
  );
}
