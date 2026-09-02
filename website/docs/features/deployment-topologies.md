# Deployment Topologies

LLM-Shield-Proxy can sit between an application and an LLM provider. It transforms detected values
before the configured upstream request and can restore mapped values on supported response paths.
Detection is not complete, so network policy and deployment tests must enforce the intended egress
boundary.

The diagrams below show two common deployment topologies.

---

## 1. Standard Egress to Cloud LLM

The application sends supported requests to the proxy inside the private network. The proxy applies
the configured detection and masking rules, then sends the transformed request to the provider.

```mermaid
flowchart LR
    subgraph VPC [Corporate VPC / Secure Boundary]
        direction LR
        App[Enterprise AI App]
        Proxy[LLM-Shield-Proxy 🛡️🔒]
        App -- "Prompt with PII" --> Proxy
    end

    subgraph Internet [External Internet]
        LLM((Cloud LLM Provider))
    end

    Proxy -- "Transformed Prompt" --> LLM
    LLM -. "Streaming Reply" .-> Proxy
    Proxy -. "Rehydrated Stream" .-> App

    style VPC fill:#f0f9ff,stroke:#0284c7,stroke-width:2px,stroke-dasharray: 5 5
    style Internet fill:#fef2f2,stroke:#ef4444,stroke-width:2px,stroke-dasharray: 5 5
    style Proxy fill:#f0fdf4,stroke:#22c55e,stroke-width:3px,color:#166534
    style LLM fill:#1e293b,stroke:#e2e8f0,color:#fff
```

### Setup Instructions
1. Deploy `LLM-Shield-Proxy` inside your VPC (e.g., via Kubernetes or Docker Compose).
2. Ensure the proxy has outbound NAT access to reach the Cloud LLM provider (e.g., `api.openai.com`).
3. Configure your AI applications to point their `base_url` to the internal proxy endpoint instead of the public LLM endpoint.

---

## 2. Internal egress gateway

For organizations whose workload network policy denies direct internet egress, LLM-Shield-Proxy can route its configured upstream client through an internal egress gateway such as Squid, Envoy, or a corporate proxy. Enforce and test the deny policy outside the application as well.

```mermaid
flowchart LR
    subgraph VPC [Air-Gapped Corporate VPC]
        direction LR
        App[Enterprise AI App]
        Proxy[LLM-Shield-Proxy 🛡️🔒]
        Egress[Internal Egress Gateway]

        App -- "Prompt with PII" --> Proxy
        Proxy -- "Sanitized Prompt" --> Egress
    end

    subgraph Internet [External Internet]
        LLM((Cloud LLM Provider))
    end

    Egress -- "Forwarded Request" --> LLM
    LLM -. "Streaming Reply" .-> Egress
    Egress -. "Streaming Reply" .-> Proxy
    Proxy -. "Rehydrated Stream" .-> App

    style VPC fill:#f0f9ff,stroke:#0284c7,stroke-width:2px,stroke-dasharray: 5 5
    style Internet fill:#fef2f2,stroke:#ef4444,stroke-width:2px,stroke-dasharray: 5 5
    style Proxy fill:#f0fdf4,stroke:#22c55e,stroke-width:3px,color:#166534
    style Egress fill:#fef9c3,stroke:#eab308,stroke-width:2px
    style LLM fill:#1e293b,stroke:#e2e8f0,color:#fff
```

### Setup Instructions
1. Deploy `LLM-Shield-Proxy` alongside your application.
2. Enable `AIR_GAPPED_MODE` and set `EGRESS_GATEWAY_URL` to the internal gateway.
3. Use firewall and DNS policy to block direct internet routes from the proxy.
4. Test every enabled provider, adapter, telemetry path, model download, update path, and failure
   path. The application setting alone does not prove an air gap or regulatory compliance.
