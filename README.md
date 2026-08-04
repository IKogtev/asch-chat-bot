# ASCH Chat Bot

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-ready-blue.svg)](https://docker.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-blue.svg)](https://postgresql.org)

## Overview

Multi-container AI chat bot system with product selection agents. Built for e-commerce use cases where you need AI to help customers choose products based on their requirements and preferences.

## Architecture

```
┌──────────┐     ┌─────────────┐     ┌──────────┐
│   User    │────▶│  FastAPI     │────▶│  Agent    │
│  Client   │◀────│   Server     │◀────│  Router   │
└──────────┘     └──────┬──────┘     └──────────┘
                        │               │
                   ┌────┴─────┐   ┌─────┴────┐
                   │PostgreSQL│   │  MCP      │
                   │  +       │   │  Tools    │
                   │Alembic   │   └───────────┘
                   └──────────┘
```

**Main components:**

- **API Server** — FastAPI backend that handles WebSocket connections and REST endpoints
- **Agent Router** — Routes user messages to appropriate agent based on conversation context
- **Product Agent** — Specialized agent for product selection, uses MCP tools to query product data
- **Conversation Agent** — Handles general chat and maintains conversation state
- **Database Layer** — PostgreSQL with Alembic migrations for schema versioning
- **MCP Integration** — Model Context Protocol for connecting to external tools

## Features

- Multi-agent architecture with specialized agents for different tasks
- WebSocket support for real-time conversation
- MCP tool integration for product data access
- PostgreSQL storage with Alembic migration system
- Docker Compose deployment with separate containers
- Conversation history and session management

## Getting Started

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- PostgreSQL 15+

### Quick Start

```bash
# Clone repository
git clone https://github.com/IKogtev/asch-chat-bot.git
cd asch-chat-bot

# Start with Docker
docker compose up

# Or run locally
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

### Configuration

Edit `.env` file:

```
DATABASE_URL=postgresql://user:pass@localhost/asch_bot
OPENAI_API_KEY=your_key
MODEL_NAME=gpt-4
```

## Project Structure

```
asch-chat-bot/
├── app/
│   ├── agents/         # Agent implementations
│   ├── api/            # FastAPI routes
│   ├── core/           # Configuration and settings
│   ├── models/         # Database models
│   └── tools/          # MCP tool definitions
├── migrations/         # Alembic migrations
├── docker-compose.yml
└── requirements.txt
```

## Tech Stack

- **Backend:** Python 3.12, FastAPI
- **Database:** PostgreSQL 15, Alembic
- **AI:** OpenAI API, MCP Protocol
- **Deployment:** Docker Compose
- **Testing:** pytest

## Usage

Connect to WebSocket endpoint or use REST API:

```bash
# Test endpoint
curl http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I need a laptop for programming"}'
```

## License

MIT
