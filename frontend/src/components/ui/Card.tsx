import clsx from "clsx";
import { HTMLAttributes } from "react";

export default function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={clsx(
        "relative rounded-2xl border border-white/[0.06] bg-surface-raised/80 bg-card-sheen p-4 shadow-card backdrop-blur-sm",
        "transition-[box-shadow,border-color,transform] duration-200 hover:border-white/[0.1] hover:shadow-card-hover",
        "sm:p-5",
        className
      )}
      {...props}
    />
  );
}
