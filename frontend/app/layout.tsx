import type { Metadata } from "next";
import "./globals.css";

export const viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "light" as const,
};

export const metadata: Metadata = {
  title: "FinScope · AI 财报投研工作台",
  description: "将上市公司年报转化为可溯源的财务研究判断",
  applicationName: "FinScope Research Console",
}; 

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
