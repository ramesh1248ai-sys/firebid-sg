import type { RouteObject } from "react-router";

import { AppShell } from "@/app/AppShell";
import { HomePage } from "@/pages/HomePage";
import { SignInPage } from "@/pages/SignInPage";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "sign-in", element: <SignInPage /> },
    ],
  },
];
