export default function Spinner({ className = "" }: { className?: string }) {
  return <div className={`h-5 w-5 animate-spin rounded-full border-2 border-surface-border border-t-accent ${className}`} />;
}
