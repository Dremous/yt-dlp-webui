import assert from "node:assert/strict";
import test from "node:test";

import { getTaskDisplay } from "./taskDisplay.js";

const downloadingMeta = { label: "下载中", color: "primary" };

test("下载阶段保留下载中的文案和绿色状态点", () => {
  const display = getTaskDisplay(
    { status: "downloading", stage: null },
    downloadingMeta
  );

  assert.equal(display.inPostprocess, false);
  assert.equal(display.label, "下载中");
  assert.equal(display.color, "primary");
  assert.equal(display.ariaLabel, "下载进度");
  assert.equal(display.dotColor, "bg-emerald-400");
});

test("有 stage 时用阶段文案覆盖下载中", () => {
  const display = getTaskDisplay(
    { status: "downloading", stage: "合并中" },
    downloadingMeta
  );

  assert.equal(display.inPostprocess, true);
  assert.equal(display.label, "合并中");
  assert.equal(display.color, "warning");
  assert.equal(display.ariaLabel, "后期处理进度");
  assert.equal(display.dotColor, "bg-amber-400");
});

test("已结束任务即使残留 stage 也不显示后期处理状态", () => {
  const display = getTaskDisplay(
    { status: "finished", stage: "合并中" },
    { label: "已完成", color: "success" }
  );

  assert.equal(display.inPostprocess, false);
  assert.equal(display.label, "已完成");
  assert.equal(display.color, "success");
  assert.equal(display.ariaLabel, "下载进度");
});

test("多流任务在下载中标出当前音频轨", () => {
  const display = getTaskDisplay(
    {
      status: "downloading",
      stage: null,
      current_stream: "audio",
      streams: [
        { kind: "video", label: "视频轨", status: "finished", progress: 100 },
        { kind: "audio", label: "音频轨", status: "downloading", progress: 26 },
      ],
    },
    downloadingMeta
  );

  assert.equal(display.label, "下载中 · 音频轨");
  assert.equal(display.streamLabel, "音频轨");
  assert.equal(display.ariaLabel, "下载进度（音频轨）");
});

test("单流任务不额外标注轨道", () => {
  const display = getTaskDisplay(
    {
      status: "downloading",
      stage: null,
      current_stream: "combined",
      streams: [
        { kind: "combined", label: "单文件", status: "downloading", progress: 42 },
      ],
    },
    downloadingMeta
  );

  assert.equal(display.label, "下载中");
  assert.equal(display.streamLabel, null);
  assert.equal(display.ariaLabel, "下载进度");
});
