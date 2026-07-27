# Marketplace submission — requirements & checklist

Distil is a streamable-HTTP MCP connector. This documents what's
needed to list it on the Claude connector directory / Vercel Marketplace.

## Machine-readable manifest
Served live at **`/manifest`** (see `core/manifest.py`) — name, description,
categories, transport, auth, capabilities, and endpoints.

## Connector basics (done)
- ✅ Live MCP endpoint: `https://distil.vercel.app/mcp`
- ✅ Streamable-HTTP transport, stateless (serverless-compatible)
- ✅ 17 tools + `metrics://summary` resource
- ✅ Public dashboards (`/`, `/engineering`) + JSON APIs (`/metrics`, `/process`)

## Required before public listing (operator to-do)
| Requirement | Status | Action |
|---|---|---|
| **Auth** on `/mcp` | ⚠️ optional now | Set `CONNECTOR_API_KEY`; for a public marketplace, OAuth 2.1 is typically required |
| **Rate limiting** | ❌ | Add before enabling paid LLM features |
| **Privacy policy + Terms URLs** | ❌ | Required by most marketplaces |
| **Data handling disclosure** | ❌ | State what prompt data is stored (currently: slim metric records only) |
| **Persistent store** | ⚠️ ephemeral | Add Upstash for durable metrics/violations |
| **Support contact / homepage** | ✅ | GitHub repo |
| **Logo / branding** | ❌ | Provide an icon |
| **Usage-based billing** (if paid) | ❌ | Metering + plan tiers |

## Categories
`developer-tools`, `security`, `observability`, `ai-infra`

## Notes
- The free heuristic tier needs no keys and no user data leaves the server.
- LLM-backed features (compression quality mode, verification, moderation,
  embeddings) require the operator's provider key and should be gated + rate-
  limited before public exposure.
