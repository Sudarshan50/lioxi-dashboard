import { ReactNode } from "react";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}

export default function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-surface-border py-16 text-center">
      {icon}
      <p className="text-sm font-medium text-gray-200">{title}</p>
      {description && <p className="max-w-sm text-xs text-gray-500">{description}</p>}
      {action}
    </div>
  );
}
