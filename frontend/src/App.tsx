import { Routes, Route, Navigate } from "react-router-dom";
import RetailerDashboard from "./pages/RetailerDashboard";
import SupplierDashboard from "./pages/SupplierDashboard";
import RoleSelection from "./pages/RoleSelection";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<RoleSelection />} />
      <Route path="/retailer" element={<RetailerDashboard />} />
      <Route path="/supplier" element={<SupplierDashboard />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
