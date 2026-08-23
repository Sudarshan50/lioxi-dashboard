import clsx from "clsx";
import { forwardRef, SelectHTMLAttributes } from "react";

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
}

const Select = forwardRef<HTMLSelectElement, SelectProps>(({ label, className, id, children, ...props }, ref) => (
  <div className="flex min-w-0 w-full flex-col gap-1.5">
    {label && (
      <label htmlFor={id} className="text-xs font-medium text-gray-400">
        {label}
      </label>
    )}
    <select
      ref={ref}
      id={id}
      className={clsx(
        "w-full min-w-0 rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-gray-100 outline-none transition-colors focus:border-accent",
        className
      )}
      {...props}
    >
      {children}
    </select>
  </div>
));

Select.displayName = "Select";
export default Select;
