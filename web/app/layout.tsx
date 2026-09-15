import type { Metadata } from "next";
import "./style.css";

export const metadata: Metadata = {
  title: "BookCast · 让阅读有回声",
  description: "本地书籍，中文播客。你的私人声音书架。",
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
