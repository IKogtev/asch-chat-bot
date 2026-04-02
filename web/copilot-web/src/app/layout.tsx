import type { Metadata } from "next";

import { CopilotKit } from "@copilotkit/react-core";

import "./globals.css";
import "@copilotkit/react-ui/styles.css";

export const metadata: Metadata = {
  title: "KB Assistant",
  description: "Web chat via CopilotKit + AG-UI (same ADK agent as Telegram)",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru">
      <body>
        <CopilotKit runtimeUrl="/api/copilotkit" agent="my_agent">
          {children}
        </CopilotKit>
      </body>
    </html>
  );
}
