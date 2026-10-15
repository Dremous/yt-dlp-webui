import React from "react";
import ReactDOM from "react-dom/client";
import { HeroUIProvider, ToastProvider } from "@heroui/react";
import App from "./App.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <HeroUIProvider>
      {/* 全局通知：替换原生 window.alert */}
      <ToastProvider placement="top-right" toastProps={{ timeout: 4000 }} />
      <main className="max-w-3xl mx-auto p-4 sm:p-6">
        <App />
      </main>
    </HeroUIProvider>
  </React.StrictMode>
);
