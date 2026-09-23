# `llm.yaml` — changing providers without changing code

This file decides which provider and model serves each task. Editing it and restarting is the
supported way to switch providers; nothing here requires a code change or a new build.

## Change a route's provider

`routes.<name>.models` is an ordered chain: primary first, then fallbacks.

```yaml
routes:
  title_block_read:
    models: [gemini-3-pro, claude-opus-5]   # Gemini now primary, Claude the fallback
```

The router tries each in order, skipping any provider that is not approved for the route's
data class or whose circuit breaker is open.

## Approve a provider for more data (decision D2)

```yaml
providers:
  openai:
    approved_data_classes: [internal, confidential, commercial]
```

The gateway checks this **before every attempt, including fallbacks**. A route that sends
`confidential` data will never reach a provider that is not approved for it — startup refuses
such a configuration, and the router refuses it again at call time.

## Add a model

```yaml
models:
  my-new-model:
    provider: openai
    model_id: <the provider's own ID>
    capabilities: [vision, structured_output, tools, streaming]
    max_output_tokens: 65536
    reasoning_parameter: reasoning_effort   # omit for models that reject it
    options: {}                              # anything else to pass through
```

Startup fails if a route requires a capability the model does not declare.

## Add a provider

A new provider needs an adapter (`ai_gateway/providers/`), which must pass the shared contract
suite in `tests/ai_gateway/test_adapter_contract.py`. That is the only case that needs code.

## Credentials

Never written here. `credentials: env://OPENAI_API_KEY` reads that environment variable;
`secret://llm/openai` reads `FIREBID_SECRET_LLM_OPENAI`.

## Checking a change

```bash
make check                          # config validation runs in the unit tests
uv run pytest -m live               # real calls, only for providers whose key is set
curl -s localhost:8000/health       # llm_routing reports the config version and providers
```

`pytest -m live` is what catches a model ID that no longer exists. It costs money and is never
part of `make check`.
