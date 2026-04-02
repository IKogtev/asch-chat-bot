import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { HttpAgent } from "@ag-ui/client";
import { NextRequest } from "next/server";

const serviceAdapter = new ExperimentalEmptyAdapter();

function normalizeAgUiBaseUrl(): string {
  const raw = (process.env.ADK_AG_UI_URL || "http://127.0.0.1:8010").trim();
  return raw.endsWith("/") ? raw : `${raw}/`;
}

const apiKey = process.env.ADK_AG_UI_API_KEY?.trim();

const runtime = new CopilotRuntime({
  agents: {
    my_agent: new HttpAgent({
      url: normalizeAgUiBaseUrl(),
      ...(apiKey ? { headers: { Authorization: `Bearer ${apiKey}` } } : {}),
    }),
  },
});

export const POST = async (req: NextRequest) => {
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter,
    endpoint: "/api/copilotkit",
  });

  return handleRequest(req);
};
