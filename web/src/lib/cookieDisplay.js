// Cookie 状态文案独立成纯函数，避免组件里堆一堆过期判断。
// 后端只返回 expires 元数据，不返回 Cookie 值；这里也不拼任何凭证。
function formatExpiry(seconds) {
  if (!seconds) return null;
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("zh-CN");
}

export function getCookieDisplay(status) {
  if (!status || !status.active) {
    return {
      label: "未导入",
      color: "default",
      description: "导入 cookies.txt 后可用于登录站点或受限内容",
    };
  }

  if (status.expired_count > 0 || status.expiry_state === "expired") {
    const expired = Number(status.expired_count || 0);
    return {
      label: expired > 0 ? `${expired} 条已过期` : "部分 Cookie 已过期",
      color: "danger",
      description: "已有 Cookie 过期，请重新导出并导入；过期的 Cookie 不会自动续期",
    };
  }

  const nearest = formatExpiry(status.nearest_expiry);
  if (status.expiring_soon || status.expiry_state === "soon") {
    return {
      label: "即将过期",
      color: "warning",
      description: nearest
        ? `最近过期时间：${nearest}，建议提前重新导入`
        : "Cookie 即将过期，建议提前重新导入",
    };
  }

  if (status.expiry_state === "session") {
    return {
      label: "已导入",
      color: "success",
      description: "包含会话 Cookie，关闭浏览器或退出登录后可能失效",
    };
  }

  return {
    label: "已导入",
    color: "success",
    description: nearest
      ? `最近过期时间：${nearest}`
      : "Cookie 已导入，将用于之后的下载请求",
  };
}
