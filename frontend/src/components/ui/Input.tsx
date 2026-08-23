import clsx from "clsx";
import { forwardRef, InputHTMLAttributes } from "react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

const Input = forwardRef<HTMLInputElement, InputProps>(({ label, className, id, ...props }, ref) => (
  <div className="flex min-w-0 w-full flex-col gap-1.5">
    {label && (
      <label htmlFor={id} className="text-xs font-medium text-gray-400">
        {label}
      </label>
    )}
    <input
      ref={ref}
      id={id}
      className={clsx(
        "w-full min-w-0 rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-gray-100 outline-none transition-colors placeholder:text-gray-600 focus:border-accent",
        className
      )}
      {...props}
    />
  </div>
));

Input.displayName = "Input";
export default Input;
