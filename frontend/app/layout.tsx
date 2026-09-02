import type { Metadata } from "next";
import Script from "next/script";
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
  icons: {
    icon: "/finscope-mark.png",
    shortcut: "/finscope-mark.png",
    apple: "/finscope-mark.png",
  },
};

// Render writes this file at container startup; Vercel uses the build-time API env instead.
const useRuntimeConfig = !process.env.VERCEL;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        {useRuntimeConfig ? <Script src="/runtime-config.js" strategy="beforeInteractive" /> : null}
        {children}
      </body>
    </html>
  );
}
