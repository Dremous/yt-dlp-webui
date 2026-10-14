// 后端 API 封装

const json = (res) => res.json().catch(() => ({}));

export const api = {
  formats: () => fetch("/api/formats").then(json),

  download: (url, format) =>
    fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, format }),
    }).then(async (res) => {
      const data = await json(res);
      if (!res.ok) throw new Error(data.detail || "下载失败");
      return data;
    }),

  tasks: () => fetch("/api/tasks").then(json),

  files: () => fetch("/api/files").then(json),

  deleteFile: (name) =>
    fetch(`/api/files/${encodeURIComponent(name)}`, { method: "DELETE" }).then(
      (r) => r.ok
    ),

  cookieStatus: () => fetch("/api/cookies/status").then(json),

  uploadCookie: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch("/api/cookies", { method: "POST", body: fd }).then(
      async (res) => {
        const data = await json(res);
        if (!res.ok) throw new Error(data.detail || "上传失败");
        return data;
      }
    );
  },

  deleteCookie: () =>
    fetch("/api/cookies", { method: "DELETE" }).then((r) => r.ok),
};
