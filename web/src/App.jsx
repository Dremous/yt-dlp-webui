import React, { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Card, CardBody, Divider } from "@heroui/react";
import { DownloadSimple } from "@phosphor-icons/react";
import { api } from "./api.js";
import { listContainer, cardItem, useMotionSafe } from "./lib/motion.js";
import DownloadForm from "./components/DownloadForm.jsx";
import CookiePanel from "./components/CookiePanel.jsx";
import ProxyPanel from "./components/ProxyPanel.jsx";
import TaskList from "./components/TaskList.jsx";
import FileList from "./components/FileList.jsx";
import AboutButton from "./components/AboutButton.jsx";

export default function App() {
  const [formats, setFormats] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [taskTotal, setTaskTotal] = useState(0);
  // 已结束任务的全量计数（后端统计，不受列表 limit 影响）
  const [taskEnded, setTaskEnded] = useState(0);
  const [files, setFiles] = useState([]);
  const [cookieOn, setCookieOn] = useState(false);
  const [proxy, setProxy] = useState({ active: false, url: "" });
  const [about, setAbout] = useState(null);

  const motionSafe = useMotionSafe();

  // 请求序号：丢弃过期响应，防止 1.5s 轮询乱序覆盖
  const tasksSeq = useRef(0);
  const filesSeq = useRef(0);

  // 加载格式、Cookie 状态、代理状态、应用信息
  useEffect(() => {
    api.formats().then(setFormats).catch((e) => console.warn("加载格式失败", e));
    api
      .cookieStatus()
      .then((s) => setCookieOn(!!s.active))
      .catch((e) => console.warn("加载 Cookie 状态失败", e));
    api
      .proxyStatus()
      .then((s) => setProxy({ active: !!s.active, url: s.url || "" }))
      .catch((e) => console.warn("加载代理状态失败", e));
    api.about().then(setAbout).catch((e) => console.warn("加载关于信息失败", e));
  }, []);

  const refreshTasks = useCallback(() => {
    const seq = ++tasksSeq.current;
    api
      .tasks(30) // 只拉最近 30 条，避免全量传输
      .then((data) => {
        if (seq === tasksSeq.current) {
          setTasks(data.tasks);
          setTaskTotal(data.total);
          setTaskEnded(data.ended ?? 0);
        }
      })
      .catch((e) => console.warn("加载任务失败", e));
  }, []);

  const refreshFiles = useCallback(() => {
    const seq = ++filesSeq.current;
    api
      .files()
      .then((data) => {
        if (seq === filesSeq.current) setFiles(data);
      })
      .catch((e) => console.warn("加载文件失败", e));
  }, []);

  // 轮询任务与文件
  useEffect(() => {
    refreshTasks();
    refreshFiles();
    const timer = setInterval(refreshTasks, 1500);
    return () => clearInterval(timer);
  }, [refreshTasks, refreshFiles]);

  const afterSubmit = useCallback(() => refreshTasks(), [refreshTasks]);

  return (
    <motion.div
      variants={listContainer}
      initial={motionSafe ? "hidden" : false}
      animate="show"
    >
      {/* 头部：品牌标记 + 绿色氛围光（直接使用 HeroUI Card 深色主题） */}
      <motion.div variants={cardItem} className="mb-5">
        <Card className="relative overflow-hidden">
          <div
            className="pointer-events-none absolute -top-16 -left-10 w-56 h-56 rounded-full bg-emerald-500/20 blur-3xl animate-glow-pulse"
            aria-hidden="true"
          />
          <CardBody className="relative px-6 py-5 flex flex-row items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <span className="flex items-center justify-center w-11 h-11 shrink-0 rounded-xl bg-gradient-to-br from-emerald-400 to-green-600 text-slate-950 shadow-lg shadow-emerald-500/30">
                <DownloadSimple size={22} weight="bold" aria-hidden="true" />
              </span>
              <div className="flex flex-col leading-tight min-w-0">
                <span className="text-xl font-bold tracking-tight text-white">
                  yt-dlp WebUI
                </span>
                <span className="text-xs text-slate-400">浏览器里的下载工作台</span>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className="hidden sm:block text-sm text-slate-400">
                粘贴链接即可下载
              </span>
              <AboutButton about={about} />
            </div>
          </CardBody>
        </Card>
      </motion.div>

      <motion.div variants={cardItem}>
        <DownloadForm formats={formats} onSubmit={afterSubmit} />
      </motion.div>

      <motion.div variants={cardItem} className="mt-4">
        <CookiePanel active={cookieOn} onChanged={setCookieOn} />
      </motion.div>

      <motion.div variants={cardItem} className="mt-4">
        <ProxyPanel
          active={proxy.active}
          current={proxy.url}
          onChanged={(active, url) => setProxy({ active, url: url || "" })}
        />
      </motion.div>

      <motion.div variants={cardItem} className="mt-4">
        <TaskList
          tasks={tasks}
          onChanged={refreshTasks}
          total={taskTotal}
          ended={taskEnded}
        />
      </motion.div>

      <motion.div variants={cardItem} className="mt-4">
        <Divider className="my-2 bg-[#26304a]" />
      </motion.div>

      <motion.div variants={cardItem} className="mt-4">
        <FileList files={files} onChanged={refreshFiles} />
      </motion.div>
    </motion.div>
  );
}
