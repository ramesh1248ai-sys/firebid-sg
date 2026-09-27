import type { RouteObject } from "react-router";

import { AppShell } from "@/app/AppShell";
import { RequireAuth } from "@/auth/RequireAuth";
import { AdminPage } from "@/pages/AdminPage";
import { AuditPage } from "@/pages/AuditPage";
import { BidDetailPage } from "@/pages/BidDetailPage";
import { CallbackPage } from "@/pages/CallbackPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { DocumentsPage } from "@/pages/DocumentsPage";
import { NewBidPage } from "@/pages/NewBidPage";
import { RegistersPage } from "@/pages/RegistersPage";
import { SheetViewerPage } from "@/pages/SheetViewerPage";
import { SignInPage } from "@/pages/SignInPage";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    children: [
      { path: "sign-in", element: <SignInPage /> },
      { path: "auth/callback", element: <CallbackPage /> },
      {
        element: <RequireAuth />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: "bids/new", element: <NewBidPage /> },
          { path: "bids/:bidId", element: <BidDetailPage /> },
          { path: "bids/:bidId/documents", element: <DocumentsPage /> },
          { path: "bids/:bidId/registers", element: <RegistersPage /> },
          { path: "bids/:bidId/sheets/:sheetId", element: <SheetViewerPage /> },
          { path: "audit", element: <AuditPage /> },
          { path: "admin", element: <AdminPage /> },
        ],
      },
    ],
  },
];
