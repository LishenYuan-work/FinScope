"use client";

import { ArrowClockwise, WarningCircle } from "@phosphor-icons/react";

export default function Error({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="route-error">
      <WarningCircle size={30} weight="fill" />
      <h1>工作台加载失败</h1>
      <p>页面暂时无法加载，请重试。</p>
      <button onClick={() => reset()}><ArrowClockwise size={16} />重新加载</button>
    </main>
  );
}
