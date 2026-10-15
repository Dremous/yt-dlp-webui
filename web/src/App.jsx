import React, { useCallback, useEffect, useRef, useState } from "react";
import { Card, CardBody, Divider, Spacer } from "@heroui/react";
import { api } from "./api.js";
import DownloadForm from "./components/DownloadForm.jsx";
import CookiePanel from "./components/CookiePanel.jsx";
import TaskList from "./components/TaskList.jsx";
import FileList from "./components/FileList.jsx";

export default function App() {
  const [formats, setFormats] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [files, setFiles] = useState([]);
  const [cookieOn, setCookieOn] = useState(false);

  // 请求序号：丢弃过期响应，防止 1.5s 轮询乱序覆盖
  const tasksSeq = useRef(0);
  const filesSeq = useRef(0);

  // 加载格式、Cookie 状态
  useEffect(() => {
    api.formats().then(setFormats).catch((e) => console.warn("加载格式失败", e));
    api
      .cookieStatus()
      .then((s) => setCookieOn(!!s.active))
      .catch((e) => console.warn("加载 Cookie 状态失败", e));
  }, []);

  const refreshTasks = useCallback(() => {
    const seq = ++tasksSeq.current;
    api
      .tasks()
      .then((data) => {
        if (seq === tasksSeq.current) setTasks(data);
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
    <>
      <Card className="mb-4 bg-[#161b2d] border border-[#29324d]">
        <CardBody className="flex flex-row items-center justify-between px-6 py-5">
          <div className="flex items-center gap-3">
            <span className="w-10 h-10 rounded-xl bg-blue-500 flex items-center justify-center text-lg">
              ▶
            </span>
            <span className="text-xl font-bold text-white">yt-dlp WebUI</span>
          </div>
          <span className="text-sm text-slate-400">粘贴链接即可下载</span>
        </CardBody>
      </Card>

      <DownloadForm formats={formats} onSubmit={afterSubmit} />

      <Spacer y={4} />

      <CookiePanel active={cookieOn} onChanged={setCookieOn} />

      <Spacer y={4} />

      <TaskList tasks={tasks} onChanged={refreshTasks} />

      <Spacer y={4} />

      <Divider className="my-2 bg-[#29324d]" />

      <Spacer y={4} />

      <FileList files={files} onChanged={refreshFiles} />
    </>
  );
}
