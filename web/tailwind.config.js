import { heroui } from "@heroui/react";

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./node_modules/@heroui/theme/dist/**/*.{js,ts,jsx,tsx}",
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      // 自定义动效关键帧（标准强度，克制不浮夸）
      keyframes: {
        // 进度条流光：一条高光横向扫过
        shimmer: {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(200%)" },
        },
        // 头部标记呼吸光晕
        "glow-pulse": {
          "0%, 100%": { opacity: "0.35", transform: "scale(1)" },
          "50%": { opacity: "0.7", transform: "scale(1.08)" },
        },
        // 状态脉冲点（下载中）
        "ping-soft": {
          "0%": { transform: "scale(1)", opacity: "0.7" },
          "70%, 100%": { transform: "scale(2.2)", opacity: "0" },
        },
        // 内容上浮入场
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        shimmer: "shimmer 1.8s ease-in-out infinite",
        "glow-pulse": "glow-pulse 3.5s ease-in-out infinite",
        "ping-soft": "ping-soft 1.6s cubic-bezier(0, 0, 0.2, 1) infinite",
        "fade-up": "fade-up 0.5s ease-out both",
      },
    },
  },
  plugins: [
    heroui({
      // 布局：略大的圆角与标准间距，贴合工具型应用的紧凑节奏
      layout: {
        radius: { small: "8px", medium: "12px", large: "16px" },
        borderWidth: { small: "1px", medium: "1px", large: "2px" },
      },
      themes: {
        dark: {
          colors: {
            // 石板灰底座（Dark Mode / OLED）
            background: "#0F172A",
            foreground: "#F8FAFC",
            content1: "#1B2336", // 卡片
            content2: "#212B42",
            content3: "#272F42", // 分隔 / muted
            content4: "#2E3850",
            default: { DEFAULT: "#272F42", foreground: "#CBD5E1" },
            // 代码绿：主 CTA 与"下载中"进度
            primary: { DEFAULT: "#22C55E", foreground: "#04210F" },
            // 已完成：偏深沉翠绿，与主绿可区分
            success: { DEFAULT: "#10B981", foreground: "#04210F" },
            danger: { DEFAULT: "#EF4444", foreground: "#FEF2F2" },
            warning: { DEFAULT: "#F59E0B", foreground: "#1C1207" },
          },
        },
      },
    }),
  ],
};
