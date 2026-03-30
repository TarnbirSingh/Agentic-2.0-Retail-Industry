// ─────────────────────────────────────────────────────────────────────────────
// Shared UI primitives — SAP Fiori-inspired light theme
// ─────────────────────────────────────────────────────────────────────────────

import React from "react";
import { cn } from "../../lib/utils";

// ── Button ────────────────────────────────────────────────────────────────────

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "success";
type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

const variantStyles: Record<ButtonVariant, string> = {
  primary:
    "bg-[#0070d2] hover:bg-[#005fb2] text-white border border-[#0070d2] shadow-sm",
  secondary:
    "bg-white hover:bg-gray-50 text-slate-700 border border-gray-300 shadow-sm",
  ghost:
    "bg-transparent hover:bg-gray-100 text-slate-600 border border-transparent",
  danger:
    "bg-red-50 hover:bg-red-100 text-red-700 border border-red-200",
  success:
    "bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200",
};

const sizeStyles: Record<ButtonSize, string> = {
  sm: "h-7 px-3 text-xs",
  md: "h-9 px-4 text-sm",
  lg: "h-10 px-5 text-sm",
};

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  className,
  disabled,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium",
        "transition-colors focus-visible:outline-none focus-visible:ring-2",
        "focus-visible:ring-[#0070d2] focus-visible:ring-offset-1 disabled:opacity-50 disabled:cursor-not-allowed",
        variantStyles[variant],
        sizeStyles[size],
        className
      )}
    >
      {loading && (
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
      )}
      {children}
    </button>
  );
}

// ── Card ──────────────────────────────────────────────────────────────────────

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  noPad?: boolean;
}

export function Card({ className, noPad, children, ...props }: CardProps) {
  return (
    <div
      {...props}
      className={cn(
        "rounded-xl border border-gray-200 bg-white shadow-sm",
        !noPad && "p-5",
        className
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      {...props}
      className={cn(
        "flex items-center justify-between border-b border-gray-100 px-5 py-4",
        className
      )}
    >
      {children}
    </div>
  );
}

export function CardBody({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div {...props} className={cn("p-5", className)}>
      {children}
    </div>
  );
}

// ── Badge ─────────────────────────────────────────────────────────────────────

type BadgeVariant =
  | "default"
  | "blue"
  | "violet"
  | "sky"
  | "emerald"
  | "amber"
  | "red"
  | "slate";

const badgeVariants: Record<BadgeVariant, string> = {
  default: "bg-gray-100 text-gray-700 border-gray-200",
  blue: "bg-blue-50 text-blue-700 border-blue-200",
  violet: "bg-violet-50 text-violet-700 border-violet-200",
  sky: "bg-sky-50 text-sky-700 border-sky-200",
  emerald: "bg-emerald-50 text-emerald-700 border-emerald-200",
  amber: "bg-amber-50 text-amber-700 border-amber-200",
  red: "bg-red-50 text-red-700 border-red-200",
  slate: "bg-slate-100 text-slate-600 border-slate-200",
};

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  dot?: boolean;
}

export function Badge({
  variant = "default",
  dot,
  className,
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      {...props}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        badgeVariants[variant],
        className
      )}
    >
      {dot && (
        <span
          className={cn("h-1.5 w-1.5 rounded-full", {
            "bg-[#0070d2]": variant === "blue",
            "bg-violet-500": variant === "violet",
            "bg-sky-500": variant === "sky",
            "bg-emerald-500": variant === "emerald",
            "bg-amber-500": variant === "amber",
            "bg-red-500": variant === "red",
            "bg-slate-400": variant === "slate" || variant === "default",
          })}
        />
      )}
      {children}
    </span>
  );
}

// ── Input ─────────────────────────────────────────────────────────────────────

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
  prefix?: string;
  suffix?: string;
}

export function Input({
  label,
  hint,
  error,
  prefix,
  suffix,
  className,
  id,
  ...props
}: InputProps) {
  const inputId = id ?? label?.toLowerCase().replace(/\s+/g, "-");
  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label
          htmlFor={inputId}
          className="text-xs font-semibold text-slate-600 uppercase tracking-wide"
        >
          {label}
        </label>
      )}
      <div className="relative flex items-center">
        {prefix && (
          <span className="absolute left-3 text-sm text-slate-400 select-none">
            {prefix}
          </span>
        )}
        <input
          id={inputId}
          {...props}
          className={cn(
            "w-full rounded-lg border border-gray-300 bg-white text-sm text-slate-900",
            "placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-[#0070d2]",
            "focus:border-[#0070d2] transition-colors h-9 px-3",
            prefix && "pl-7",
            suffix && "pr-10",
            error && "border-red-400 focus:ring-red-400",
            className
          )}
        />
        {suffix && (
          <span className="absolute right-3 text-sm text-slate-400 select-none">
            {suffix}
          </span>
        )}
      </div>
      {hint && !error && (
        <p className="text-xs text-slate-500">{hint}</p>
      )}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}

// ── Textarea ──────────────────────────────────────────────────────────────────

interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  hint?: string;
  error?: string;
}

export function Textarea({
  label,
  hint,
  error,
  className,
  id,
  ...props
}: TextareaProps) {
  const textareaId = id ?? label?.toLowerCase().replace(/\s+/g, "-");
  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label
          htmlFor={textareaId}
          className="text-xs font-semibold text-slate-600 uppercase tracking-wide"
        >
          {label}
        </label>
      )}
      <textarea
        id={textareaId}
        {...props}
        className={cn(
          "w-full rounded-lg border border-gray-300 bg-white text-sm text-slate-900",
          "placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-[#0070d2]",
          "focus:border-[#0070d2] transition-colors px-3 py-2.5 resize-none",
          error && "border-red-400 focus:ring-red-400",
          className
        )}
      />
      {hint && !error && (
        <p className="text-xs text-slate-500">{hint}</p>
      )}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}

// ── Select ────────────────────────────────────────────────────────────────────

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
}

export function Select({ label, className, id, children, ...props }: SelectProps) {
  const selectId = id ?? label?.toLowerCase().replace(/\s+/g, "-");
  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label
          htmlFor={selectId}
          className="text-xs font-semibold text-slate-600 uppercase tracking-wide"
        >
          {label}
        </label>
      )}
      <select
        id={selectId}
        {...props}
        className={cn(
          "w-full rounded-lg border border-gray-300 bg-white text-sm text-slate-900",
          "focus:outline-none focus:ring-2 focus:ring-[#0070d2] focus:border-[#0070d2]",
          "transition-colors h-9 px-3 appearance-none",
          className
        )}
      >
        {children}
      </select>
    </div>
  );
}

// ── Divider ───────────────────────────────────────────────────────────────────

export function Divider({ className }: { className?: string }) {
  return <hr className={cn("border-gray-200", className)} />;
}

// ── Spinner ───────────────────────────────────────────────────────────────────

export function Spinner({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  const s = { sm: "h-4 w-4", md: "h-6 w-6", lg: "h-8 w-8" }[size];
  return (
    <span
      className={cn(
        s,
        "animate-spin rounded-full border-2 border-gray-200 border-t-[#0070d2]"
      )}
    />
  );
}

// ── Alert ─────────────────────────────────────────────────────────────────────

type AlertVariant = "info" | "warning" | "error" | "success";

const alertStyles: Record<AlertVariant, string> = {
  info: "bg-blue-50 border-blue-200 text-blue-800",
  warning: "bg-amber-50 border-amber-200 text-amber-800",
  error: "bg-red-50 border-red-200 text-red-800",
  success: "bg-emerald-50 border-emerald-200 text-emerald-800",
};

export function Alert({
  variant = "info",
  title,
  children,
  className,
}: {
  variant?: AlertVariant;
  title?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border px-4 py-3 text-sm",
        alertStyles[variant],
        className
      )}
    >
      {title && <p className="font-semibold mb-1">{title}</p>}
      <div className="text-sm opacity-90">{children}</div>
    </div>
  );
}