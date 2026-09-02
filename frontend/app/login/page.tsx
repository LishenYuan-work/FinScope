"use client";

import { ArrowRight, Eye, EyeSlash, LockKey, UserCircle } from "@phosphor-icons/react";
import { FormEvent, useState } from "react";
import { saveAccessToken } from "../../lib/auth";

const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
const isLocalApiBase = !configuredApiBase || /^(https?:\/\/)?(localhost|127\.0\.0\.1)(:\d+)?\/?$/i.test(configuredApiBase);
const API_BASE = process.env.VERCEL && isLocalApiBase
  ? "https://finscope-backend-637y.onrender.com"
  : configuredApiBase ?? "http://localhost:8000";

declare global {
  interface Window {
    __FINSCOPE_CONFIG__?: { apiBase?: string };
  }
}

function apiBaseUrl() {
  if (typeof window !== "undefined") return window.__FINSCOPE_CONFIG__?.apiBase?.replace(/\/$/, "") || API_BASE;
  return API_BASE;
}

export default function LoginPage() {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const response = await fetch(`${apiBaseUrl()}/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const payload = (await response.json().catch(() => ({}))) as { access_token?: string; message?: string; detail?: string };
      if (!response.ok) throw new Error(payload.detail || payload.message || "认证失败，请稍后重试");
      if (mode === "signup" && !payload.access_token) {
        setMessage(payload.message || "注册成功，请检查邮箱完成验证后再登录");
        setMode("login");
        setPassword("");
        return;
      }
      if (!payload.access_token) throw new Error("认证服务未返回登录凭证");
      saveAccessToken(payload.access_token);
      window.location.assign("/");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "认证失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand"><span className="auth-brand-mark"><UserCircle size={22} weight="duotone" /></span><span>FinScope</span></div>
        <div className="auth-kicker">AI RESEARCH STUDIO</div>
        <h1 id="auth-title">进入你的投研工作台</h1>
        <p className="auth-subtitle">使用邮箱和密码保存并管理你的财报分析任务。</p>
        <div className="auth-tabs" role="tablist" aria-label="认证方式">
          <button type="button" className={mode === "login" ? "is-active" : ""} onClick={() => { setMode("login"); setError(""); setMessage(""); }}>登录</button>
          <button type="button" className={mode === "signup" ? "is-active" : ""} onClick={() => { setMode("signup"); setError(""); setMessage(""); }}>注册</button>
        </div>
        <form className="auth-form" onSubmit={submit}>
          <label>邮箱<input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" /></label>
          <label>密码<div className="password-field"><input type={showPassword ? "text" : "password"} autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={8} required value={password} onChange={(event) => setPassword(event.target.value)} placeholder="至少 8 位字符" /><button type="button" aria-label={showPassword ? "隐藏密码" : "显示密码"} onClick={() => setShowPassword((value) => !value)}>{showPassword ? <EyeSlash size={18} /> : <Eye size={18} />}</button></div></label>
          {error && <div className="auth-alert auth-alert-error">{error}</div>}
          {message && <div className="auth-alert auth-alert-success">{message}</div>}
          <button className="auth-submit" disabled={busy} type="submit"><LockKey size={18} />{busy ? "处理中…" : mode === "login" ? "登录工作台" : "创建账户"}<ArrowRight size={17} /></button>
        </form>
        <p className="auth-footnote">账户由 Supabase Auth 安全托管，密码不会写入本项目数据库。</p>
      </section>
    </main>
  );
}
