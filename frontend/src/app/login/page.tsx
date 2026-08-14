"use client";

import React, { useState } from "react";
import { flushSync } from "react-dom";
import { CheckCircle2, Eye, EyeOff, Lock, User, Loader2, ShieldCheck } from "lucide-react";

type LoginState = "idle" | "verifying" | "redirecting";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loginState, setLoginState] = useState<LoginState>("idle");
  const isSubmitting = loginState !== "idle";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoginState("verifying");

    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ username, password }),
      });

      const data = await res.json();

      if (res.ok) {
        flushSync(() => setLoginState("redirecting"));
        window.location.replace("/users");
        return;
      }

      setError(data.error || "登录失败，请检查用户名和密码");
      setLoginState("idle");
    } catch (err) {
      setError("网络连接错误，请稍后重试");
      setLoginState("idle");
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-950 px-4 py-12">
      {/* Ambient depth: single-hue radial washes + fine grid, no glass */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage:
            "linear-gradient(to right, #fff 1px, transparent 1px), linear-gradient(to bottom, #fff 1px, transparent 1px)",
          backgroundSize: "48px 48px",
          maskImage: "radial-gradient(ellipse 80% 60% at 50% 40%, black, transparent)",
          WebkitMaskImage: "radial-gradient(ellipse 80% 60% at 50% 40%, black, transparent)",
        }}
      />
      <div className="pointer-events-none absolute -top-24 left-1/2 h-96 w-96 -translate-x-1/2 rounded-full bg-cyan-600/15 blur-[130px]" />
      <div className="pointer-events-none absolute bottom-0 right-1/4 h-80 w-80 rounded-full bg-cyan-800/10 blur-[130px]" />

      <div className="relative z-10 w-full max-w-sm">
        {/* Brand */}
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="relative mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-400 to-cyan-600 text-2xl font-black text-cyan-950 shadow-lg shadow-cyan-500/25 ring-1 ring-inset ring-white/25">
            R
            <span className="absolute -bottom-1 -right-1 h-3.5 w-3.5 rounded-full border-2 border-slate-950 bg-emerald-400" />
          </span>
          <h1 className="text-2xl font-extrabold tracking-tight text-white">燃人AI 2.0</h1>
          <p className="mt-2 text-sm text-slate-400">内部智能社群运营系统</p>
        </div>

        {/* Form card — solid, not glass */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900 p-7 shadow-2xl shadow-black/40">
          <form
            onSubmit={handleSubmit}
            method="post"
            aria-busy={isSubmitting}
            className="space-y-5"
          >
            {error && (
              <div
                role="alert"
                className="rounded-xl border border-rose-500/25 bg-rose-500/10 px-3.5 py-3 text-sm font-medium text-rose-300"
              >
                {error}
              </div>
            )}

            {/* Username */}
            <div>
              <label htmlFor="username" className="mb-1.5 block text-xs font-semibold text-slate-300">
                用户名
              </label>
              <div className="relative">
                <User className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                <input
                  id="username"
                  name="username"
                  type="text"
                  required
                  disabled={isSubmitting}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="h-11 w-full rounded-xl border border-slate-700 bg-slate-950 pl-10 pr-3.5 text-sm font-medium text-white outline-none transition-all duration-150 placeholder:text-slate-500 hover:border-slate-600 focus:border-cyan-500 focus:ring-4 focus:ring-cyan-500/20"
                  placeholder="请输入用户名"
                />
              </div>
            </div>

            {/* Password */}
            <div>
              <label htmlFor="password" className="mb-1.5 block text-xs font-semibold text-slate-300">
                密码
              </label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                <input
                  id="password"
                  name="password"
                  type={showPassword ? "text" : "password"}
                  required
                  disabled={isSubmitting}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="h-11 w-full rounded-xl border border-slate-700 bg-slate-950 pl-10 pr-11 text-sm font-medium text-white outline-none transition-all duration-150 placeholder:text-slate-500 hover:border-slate-600 focus:border-cyan-500 focus:ring-4 focus:ring-cyan-500/20"
                  placeholder="请输入密码"
                />
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  className="absolute right-1.5 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-300"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            {/* Submit — solid teal, no rainbow gradient */}
            <button
              type="submit"
              disabled={isSubmitting}
              className={`flex h-11 w-full items-center justify-center gap-2 rounded-xl px-4 text-sm font-bold text-white outline-none transition-all duration-150 focus-visible:ring-4 active:scale-[0.99] disabled:pointer-events-none ${
                loginState === "redirecting"
                  ? "bg-emerald-600 shadow-lg shadow-emerald-950/40 focus-visible:ring-emerald-500/30"
                  : "bg-cyan-600 shadow-lg shadow-cyan-900/40 hover:bg-cyan-500 focus-visible:ring-cyan-500/30 disabled:opacity-60"
              }`}
            >
              {loginState === "redirecting" ? (
                <>
                  <CheckCircle2 className="h-4 w-4" />
                  <span aria-live="polite">登录成功，正在进入系统…</span>
                </>
              ) : loginState === "verifying" ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
                  <span aria-live="polite">正在验证…</span>
                </>
              ) : (
                <span>登 录</span>
              )}
            </button>
          </form>
        </div>

        {/* Footer */}
        <p className="mt-7 flex items-center justify-center gap-1.5 text-center text-xs text-slate-500">
          <ShieldCheck className="h-3.5 w-3.5" />
          燃人AI 内部系统 · 安全受控访问
        </p>
      </div>
    </div>
  );
}
