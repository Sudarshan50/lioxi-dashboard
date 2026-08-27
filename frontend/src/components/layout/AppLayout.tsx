import { Menu } from "lucide-react";
import { useState } from "react";
import { Outlet } from "react-router-dom";

import { useO1NewApiAuth } from "@/hooks/useKimiDeploy";

import Sidebar from "./Sidebar";

export default function AppLayout() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const o1Auth = useO1NewApiAuth();
  const tokenExpired = Boolean(o1Auth.data?.auth_expired);

  return (
    <div className="app-aurora flex h-screen bg-surface">
      <Sidebar isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        {tokenExpired && (
          <div
            role="alert"
            className="sticky top-0 z-50 animate-token-flash border-b border-red-400/50 bg-red-600/90 px-4 py-2.5 text-center text-sm font-semibold tracking-wide text-white shadow-glow"
          >
            O1 portal token expired
          </div>
        )}
        <header className="flex items-center gap-3 border-b border-white/[0.06] bg-surface-raised/70 px-4 py-3 backdrop-blur-xl lg:hidden">
          <button
            onClick={() => setIsSidebarOpen(true)}
            className="rounded-md p-1.5 text-gray-300 hover:bg-surface-border hover:text-gray-100"
            aria-label="Open menu"
          >
            <Menu size={20} />
          </button>
          <p className="text-sm font-semibold text-gray-50">Usage Portal</p>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
          <div className="mx-auto w-full max-w-7xl animate-fade-up">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
