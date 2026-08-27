import clsx from "clsx";
import { BellRing, Cloud, Cpu, Inbox, LayoutDashboard, LogOut, Rocket, X } from "lucide-react";
import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/context/AuthContext";
import apiClient from "@/lib/apiClient";
import { PendingListResponse } from "@/types";

const navItems = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/accounts", label: "Accounts", icon: Cloud },
  { to: "/deploy", label: "Deploy K3", icon: Rocket },
  { to: "/pending", label: "Pending", icon: Inbox },
  { to: "/models", label: "Models", icon: Cpu },
  { to: "/alerts", label: "Alerts", icon: BellRing },
];

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function Sidebar({ isOpen, onClose }: SidebarProps) {
  const { logout } = useAuth();
  const pending = useQuery({
    queryKey: ["pending-submits"],
    queryFn: async () => (await apiClient.get<PendingListResponse>("/api/pending")).data,
    refetchInterval: 30_000,
  });
  const failedCount = pending.data?.failed_count ?? 0;
  const waitingCount = pending.data?.pending_count ?? 0;

  return (
    <>
      {isOpen && (
        <div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden" onClick={onClose} aria-hidden="true" />
      )}
      <aside
        className={clsx(
          "fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r border-white/[0.06] bg-surface-raised/70 px-4 py-6 backdrop-blur-xl transition-transform duration-200 lg:static lg:translate-x-0",
          isOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="mb-8 flex items-center justify-between px-2">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent-gradient text-white shadow-glow-sm">
              <Cpu size={18} />
            </div>
            <div>
              <p className="text-sm font-semibold leading-tight text-gray-50">Usage Portal</p>
              <p className="text-[11px] leading-tight text-gray-500">Azure OpenAI monitoring</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-gray-400 hover:bg-surface-border hover:text-gray-100 lg:hidden">
            <X size={18} />
          </button>
        </div>
        <nav className="flex flex-1 flex-col gap-1.5">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={onClose}
              className={({ isActive }) =>
                clsx(
                  "group relative flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition-all duration-150",
                  isActive
                    ? "bg-accent/15 text-indigo-200 shadow-[inset_0_0_0_1px_rgba(99,102,241,0.35)]"
                    : "text-gray-400 hover:bg-white/[0.04] hover:text-gray-100"
                )
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-accent-gradient" />
                  )}
                  <Icon size={17} className={clsx("transition-transform duration-150 group-hover:scale-110", isActive && "text-indigo-300")} />
                  <span className="flex-1">{label}</span>
                  {to === "/pending" && (failedCount > 0 || waitingCount > 0) && (
                    <span
                      className={clsx(
                        "min-w-[1.25rem] rounded-full px-1.5 py-0.5 text-center text-[10px] font-semibold",
                        failedCount > 0 ? "bg-red-500/20 text-red-300" : "bg-accent/20 text-indigo-200"
                      )}
                    >
                      {failedCount > 0 ? failedCount : waitingCount}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <button
          onClick={logout}
          className="flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium text-gray-400 transition-colors hover:bg-white/[0.04] hover:text-gray-100"
        >
          <LogOut size={17} />
          Log out
        </button>
      </aside>
    </>
  );
}
