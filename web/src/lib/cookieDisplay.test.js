import { test } from "node:test";
import assert from "node:assert/strict";
import { getCookieDisplay } from "./cookieDisplay.js";

test("未导入 Cookie 时显示默认状态", () => {
  const display = getCookieDisplay({ active: false });
  assert.equal(display.label, "未导入");
  assert.equal(display.color, "default");
});

test("有过期 Cookie 时明确提示重新导入", () => {
  const display = getCookieDisplay({
    active: true,
    expired_count: 2,
    expiry_state: "expired",
  });
  assert.equal(display.color, "danger");
  assert.match(display.label, /过期/);
  assert.match(display.description, /重新导出|重新导入/);
});

test("即将过期时显示最近过期日期", () => {
  const display = getCookieDisplay({
    active: true,
    expiring_soon: true,
    nearest_expiry: 1893456000,
  });
  assert.equal(display.color, "warning");
  assert.match(display.label, /即将过期/);
  assert.match(display.description, /最近过期/);
});

test("只有会话 Cookie 时提示关闭浏览器会失效", () => {
  const display = getCookieDisplay({
    active: true,
    expiry_state: "session",
  });
  assert.equal(display.color, "success");
  assert.match(display.description, /会话 Cookie/);
});

test("普通有效 Cookie 显示已导入", () => {
  const display = getCookieDisplay({
    active: true,
    expiry_state: "ok",
    nearest_expiry: 1893456000,
  });
  assert.equal(display.label, "已导入");
  assert.equal(display.color, "success");
});
