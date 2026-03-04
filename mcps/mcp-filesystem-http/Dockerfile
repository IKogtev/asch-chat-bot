FROM node:22-alpine
# mcp-proxy — это стабильный общественный мост
RUN npm install -g @modelcontextprotocol/server-filesystem mcp-proxy
WORKDIR /app
EXPOSE 8080
# Используем mcp-proxy для перевода stdio в sse
ENTRYPOINT ["mcp-proxy", "npx", "@modelcontextprotocol/server-filesystem", "/mcp-filesystem/kb"]