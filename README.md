# ASCH Chat Bot

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-blue.svg)](https://www.postgresql.org)
[![Docker](https://img.shields.io/badge/Docker-native-blue.svg)](https://www.docker.com)
[![Google ADK](https://img.shields.io/badge/Google%20ADK-agent-orange.svg)](https://developers.google.com)

Multi-container AI-powered chat bot with product selection agents, knowledge base search, and MCP tool integration. Built for enterprise customer support workflows.

## Architecture

```text
                    ┌─────────────────────────────────────┐
                    │           User (Telegram/Web)        │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │         Bot Container                │
                    │  (Telegram API / Web Gateway)        │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │      Agent Container                 │
                    │                                      │
                    │  ┌──────────────┐  ┌───────────────┐│
                    │  │ Root Agent   │──│ Product Agent ││
                    │  └──────┬───────┘  └──────┬────────┘│
                    │         │                  │         │
                    │  ┌──────▼────────┐  ┌─────▼───────┐ │
                    │  │ Knowledge Base│  │ DB Search   │ │
                    │  │ (MCP Server)  │  │ (MCP Server)│ │
                    │  └───────────────┘  └─────────────┘ │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │        PostgreSQL                    │
                    │  (Sessions, State, Product DB)       │
                    └─────────────────────────────────────┘
```

## Key Features

- **Multi-agent architecture** — Root agent orchestrates specialized sub-agents (product selection, knowledge base, validation)
- **MCP tool integration** — Connects to external MCP servers for knowledge base search and database queries
- **Product selection workflow** — Intelligent product recommendation with dialog-based navigation and fallback chains
- **Session persistence** — PostgreSQL-backed session management via Google ADK Session Service
- **Alembic migrations** — Database schema versioning with automated migrations
- **Docker Compose deployment** — Two-container setup (bot + agent) with shared PostgreSQL
- **Smart fallback** — Graceful degradation when tools are unavailable or queries are ambiguous

## Quick Start

### Prerequisites

- Docker & Docker Compose
- PostgreSQL credentials (configured via environment)

### Setup

```bash
# Clone and configure
git clone https://github.com/IKogtev/asch-chat-bot.git
cd asch-chat-bot

# Set environment variables (see .env.example)
# Configure bot credentials, agent settings, PostgreSQL connection

# Start all services
docker compose up -d

# Run database migrations
docker compose exec agent alembic upgrade head

# Verify
docker compose ps
```

### Docker Services

| Service | Container | Port | Purpose |
|---------|-----------|------|---------|
| Bot | `bot` | — | Telegram/Web gateway |
| Agent | `agent` | — | AI agent orchestration |
| PostgreSQL | — | 5432 | Sessions, state, product DB |

## Project Structure

```
asch-chat-bot/
├── agent/                    # AI agent service
│   ├── agents/               # Specialized agents (product, validation)
│   ├── tools/                # MCP tool implementations
│   ├── config.py             # Agent configuration
│   ├── rootagent.py          # Root orchestrator
│   ├── smart_fallback.py     # Fallback chain logic
│   └── start_agent.py        # Agent entry point
├── bot/                      # Bot gateway service
├── mcps/                     # MCP server configurations
├── alembic/                  # Database migrations
│   └── versions/
├── kb_storage/               # Knowledge base storage
├── docs/                     # Documentation
│   ├── agents-chain.md       # Agent chain architecture
│   ├── connect_mcps.md       # MCP integration guide
│   └── release_notes/
├── tests/                    # Test suite
├── docker-compose.yaml       # Multi-container setup
├── Dockerfile.agent          # Agent container
└── Dockerfile.bot            # Bot container
```

## Technology Stack

- **AI/Agent:** Google ADK, Multi-Agent Systems, MCP (Model Context Protocol)
- **Backend:** Python 3.12, FastAPI
- **Database:** PostgreSQL 15, Alembic (migrations)
- **Deployment:** Docker Compose, GitLab CI/CD
- **Messaging:** Telegram Bot API
- **Tools:** Knowledge base search (vector embeddings), Database search (SQLite/PostgreSQL)

## Agent Chain

The bot uses a hierarchical agent architecture:

1. **Root Agent** — Entry point, analyzes user intent, routes to specialized agents
2. **Product Selection Agent** — Guides users through product selection with dialog-based interaction
3. **Knowledge Base Agent** — Semantic search across documentation and KB articles
4. **Validation Agent** — Cross-checks recommendations against product database
5. **Smart Fallback** — Graceful fallback chain when confidence is low

See [docs/agents-chain.md](docs/agents-chain.md) for detailed architecture.

## Testing

```bash
# Run test suite
pytest -v

# Run specific test categories
pytest tests/ -k "product" -v
pytest tests/ -k "kb" -v
```

## Documentation

- [Agent Chain Architecture](docs/agents-chain.md)
- [MCP Integration Guide](docs/connect_mcps.md)
- [Product Selection Agent](docs/product-selection-agent-implementation-plan-v2.md)
- [Migration Guide](docs/migration_guide.md)
- [Release Notes](docs/release_notes/)
- [ADK Session Postgres DevOps](docs/adk-session-postgres-devops.md)

## Author

**Itai Kogtev** — Forward Deployed AI Engineer

[GitHub](https://github.com/IKogtev) · [LinkedIn](https://linkedin.com/in/vkogtev)
