# OmniQQ-MCP roadmap

## Released in v2.0.0

- LLOneBot backend with LLBot CLI / Desktop / LiteLoaderQQNT layout support, WebUI QR login and `ob11.connect` configuration.
- Streamable HTTP MCP transport (sessions, SSE, DELETE, protocol negotiation, bearer auth, CORS) as the default; stdio compatibility mode retained.
- 74 named tools preserved; 8 NapCat tool-name compatibility aliases; core/full profiles expose 26/82 tools.
- Account-isolated event caches, ordered bounded event hooks, metadata-only audit rotation and safer deployment rollback.

## Released in v1.1.0

- 74 named QQ tools and OneBot Action passthrough.
- Account-isolated event caches, concurrent stdio requests and read-only cancellation.
- Ordered bounded event hooks, private Token storage and metadata-only audit rotation.
- Safer runtime deployment, account configuration and targeted process control.

## Planned

- Add one-click adapters for the llonebot.nix `sandbox` and Docker image (container lifecycle, port mapping, WebUI token injection).
- Validate more MCP Streamable HTTP client implementations and OAuth-based remote authorization.
- Test live WebSocket connections across the declared dependency range and LLOneBot releases.
- Version the Action catalog against LLOneBot releases and add live capability discovery.
- Expand packaging and remote-endpoint compatibility checks across platforms.

These are proposals, not features shipped in the current release.
