"use client";

import { CopilotChat } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";

export default function HomePage() {
  return (
    <main>
      <h1>База знаний</h1>
      <p className="lead">
        Чат с тем же агентом ADK и MCP, что и Telegram-бот. Откройте панель справа.
      </p>
      <CopilotChat
        agentId="my_agent"
        labels={{
          chatInputPlaceholder:"Задайте вопрос по базе знаний"
        }}
      />
    </main>
  );
}
