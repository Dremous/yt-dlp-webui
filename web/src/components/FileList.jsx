import React from "react";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Table,
  TableHeader,
  TableColumn,
  TableBody,
  TableRow,
  TableCell,
} from "@heroui/react";
import { api } from "../api.js";

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
  async function handleDelete(name) {
    if (!window.confirm(`确定删除「${name}」？`)) return;
    try {
      await api.deleteFile(name);
      onChanged();
    } catch (e) {
      window.alert("删除失败：" + e.message);
    }
  }

  return (
    <Card className="card-ui">
      <CardHeader className="px-5 pt-5 flex items-center justify-between">
        <h2 className="text-base font-semibold text-white">已下载文件</h2>
        <Button
          size="sm"
          variant="flat"
          color="default"
          onPress={onChanged}
          className="transition-ui"
        >
          ↻ 刷新
        </Button>
      </CardHeader>
      <CardBody className="px-5 pb-5">
        {files.length === 0 ? (
          <p className="text-sm text-slate-500 text-center py-4">暂无文件</p>
        ) : (
          <Table
            aria-label="已下载文件"
            removeWrapper
            className="text-sm"
          >
            <TableHeader>
              <TableColumn>文件名</TableColumn>
              <TableColumn>大小</TableColumn>
              <TableColumn>下载时间</TableColumn>
              <TableColumn align="end">操作</TableColumn>
            </TableHeader>
            <TableBody>
              {files.map((f) => (
                <TableRow key={f.name}>
                  <TableCell>
                    <span className="block max-w-[300px] truncate text-slate-200">
                      {f.name}
                    </span>
                  </TableCell>
                  <TableCell>{formatSize(f.size)}</TableCell>
                  <TableCell>{formatTime(f.mtime)}</TableCell>
                  <TableCell>
                    <div className="flex gap-2 justify-end">
                      <a href={`/files/${encodeURIComponent(f.name)}`}>
                        <Button size="sm" color="success" variant="flat" className="transition-ui">
                          下载
                        </Button>
                      </a>
                      <Button
                        size="sm"
                        color="danger"
                        variant="light"
                        onPress={() => handleDelete(f.name)}
                        className="transition-ui"
                      >
                        删除
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardBody>
    </Card>
  );
}
