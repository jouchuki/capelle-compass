/**
 * DataKompas — v2 shell.
 *
 * Routes:
 *   /                AppRoute     — chat + side artifact (the main UX)
 *   /login           LoginRoute   — auth landing
 *   /admin/*         AdminRoute   — operator dashboard (nested tabs)
 *   /f/:token        ForkRoute    — read-only shared analysis
 *   *                NotFound
 *
 * Real backend + mock mode: see src/mocks/index.ts. Append ?mock=1 (or set
 * VITE_MOCK_MODE=1) to drive the entire app from canned fixtures.
 */

import { BrowserRouter, Routes, Route } from 'react-router-dom';
import AppRoute from './routes/AppRoute';
import LoginRoute from './routes/LoginRoute';
import AdminRoute from './routes/AdminRoute';
import ForkRoute from './routes/ForkRoute';
import NotFoundRoute from './routes/NotFoundRoute';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppRoute />} />
        <Route path="/login" element={<LoginRoute />} />
        <Route path="/admin/*" element={<AdminRoute />} />
        <Route path="/f/:token" element={<ForkRoute />} />
        <Route path="*" element={<NotFoundRoute />} />
      </Routes>
    </BrowserRouter>
  );
}
