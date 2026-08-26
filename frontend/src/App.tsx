import { Navigate, Route, Routes } from "react-router-dom";

import AppLayout from "@/components/layout/AppLayout";
import AccountsPage from "@/pages/AccountsPage";
import AlertsPage from "@/pages/AlertsPage";
import DeployK3Page from "@/pages/DeployK3Page";
import LoginPage from "@/pages/LoginPage";
import ModelsPage from "@/pages/ModelsPage";
import OverviewPage from "@/pages/OverviewPage";
import ProtectedRoute from "@/routes/ProtectedRoute";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/deploy" element={<DeployK3Page />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
