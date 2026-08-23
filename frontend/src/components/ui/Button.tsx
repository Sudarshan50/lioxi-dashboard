import clsx from "clsx";
import { ButtonHTMLAttributes, forwardRef } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  isLoading?: boolean;
}

const variantClasses: Record<Variant, string> = {
  primary: "bg-accent-gradient text-white shadow-btn-glow hover:brightness-110 active:brightness-95",
  secondary:
    "bg-surface-raised/80 text-gray-100 border border-white/[0.08] backdrop-blur-sm hover:border-accent/40 hover:bg-surface-hover",
  ghost: "bg-transparent hover:bg-surface-raised text-gray-300",
  danger: "bg-red-600/90 hover:bg-red-500 text-white shadow-card",
};

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "primary", isLoading, className, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || isLoading}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60",
        variantClasses[variant],
        className
      )}
      {...props}
    >
      {isLoading && (
        <span
          className={clsx(
            "h-3.5 w-3.5 animate-spin rounded-full border-2",
            variant === "secondary" || variant === "ghost"
              ? "border-gray-500/40 border-t-gray-100"
              : "border-white/40 border-t-white"
          )}
        />
      )}
      {children}
    </button>
  )
);

Button.displayName = "Button";
export default Button;
