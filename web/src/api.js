// 后端 API 封装

const json = (res) => res.json().catch(() => ({}));

// 统一检查响应状态：非 2xx 时抛出后端 detail（或默认消息），避免前端拿到无效数据崩溃
const check = async (res, msg) => {
  const data = await json(res);
  if (!res.ok) {
    // pydantic 校验失败（如 URL 超长）的 detail 是数组，取首条 msg；否则为字符串
    const detail = data.detail;
    const detailMsg = Array.isArray(detail) ? detail[0]?.msg : detail;
    throw new Error(detailMsg || msg);
  }
  return data;
};

export const api = {
  formats: () => fetch("/api/formats").then((r) => check(r, "加载格式失败")),

  download: (url, format) =>
    fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, format }),
    }).then((r) => check(r, "下载失败")),

  tasks: (limit) =>
    fetch(`/api/tasks${limit ? `?limit=${limit}` : ""}`).then((r) =>
      check(r, "加载任务失败")
    ),

  files: () => fetch("/api/files").then((r) => check(r, "加载文件失败")),

  cancelTask: (id) =>
    fetch(`/api/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" }).then(
      (r) => check(r, "取消失败")
    ),

  deleteFile: (name) =>
    fetch(`/api/files/${encodeURIComponent(name)}`, { method: "DELETE" }).then(
      (r) => check(r, "删除失败")
    ),

  cookieStatus: () =>
    fetch("/api/cookies/status").then((r) => check(r, "加载 Cookie 状态失败")),

  uploadCookie: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch("/api/cookies", { method: "POST", body: fd }).then((r) =>
      check(r, "上传失败")
    );
  },

  deleteCookie: () =>
    fetch("/api/cookies", { method: "DELETE" }).then((r) => check(r, "清除失败")),
};
