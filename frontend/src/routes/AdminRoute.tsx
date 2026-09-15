// Admin emits no telemetry — keep this surface invisible to event tracking.
/**
 * DataKompas — /admin shell.
 *
 * Renders the admin dashboard with 7 nested tabs:
 *   /admin             OverviewTab
 *   /admin/questions   QuestionsTab
 *   /admin/feedback    FeedbackTab
 *   /admin/tokens      TokensTab
 *   /admin/engagement  EngagementTab
 *   /admin/users       UsersTab
 *   /admin/codex       CodexTab
 *
 * The shell wraps every child in ``AdminShell`` which gates access via
 * ``useAdminCheck`` and provides the sticky header + tab bar.
 */

import { Routes, Route } from 'react-router-dom';
import AdminShell from '../components/admin/AdminShell';
import OverviewTab from '../components/admin/tabs/OverviewTab';
import QuestionsTab from '../components/admin/tabs/QuestionsTab';
import FeedbackTab from '../components/admin/tabs/FeedbackTab';
import TokensTab from '../components/admin/tabs/TokensTab';
import EngagementTab from '../components/admin/tabs/EngagementTab';
import UsersTab from '../components/admin/tabs/UsersTab';
import CodexTab from '../components/admin/tabs/CodexTab';

export default function AdminRoute() {
  return (
    <AdminShell>
      <Routes>
        <Route index element={<OverviewTab />} />
        <Route path="questions" element={<QuestionsTab />} />
        <Route path="feedback" element={<FeedbackTab />} />
        <Route path="tokens" element={<TokensTab />} />
        <Route path="engagement" element={<EngagementTab />} />
        <Route path="users" element={<UsersTab />} />
        <Route path="codex" element={<CodexTab />} />
      </Routes>
    </AdminShell>
  );
}
