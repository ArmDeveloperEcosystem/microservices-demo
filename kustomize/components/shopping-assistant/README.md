# Shopping Assistant Component

This component adds `shoppingassistantservice` to the Online Boutique storefront.

The current workshop version is intentionally lightweight:

- the assistant is a plain Flask service
- it uses explicit tool helpers for `search_catalog`, `get_product_details`, `get_cart`, and `add_to_cart`
- it stays grounded in the live storefront services
- it uses a local Ollama sidecar for the reasoning step
- it does **not** depend on AlloyDB, RAG, or a separate vector-search layer

## What this component contains

- `kustomization.yaml`
  - enables the assistant component and patches `frontend` so the assistant experience is visible in the storefront
- `shoppingassistantservice.yaml`
  - defines the assistant deployment, service, and local Ollama sidecar

## Workshop usage

In the Axion workshop, learners:

1. clone the official Arm-maintained workshop repo
2. build and push their assistant image to Artifact Registry
3. apply an overlay that enables this component on `N4A`
4. validate the assistant through the storefront
5. move only the assistant to `C4A`
6. rerun the same benchmark to compare placement

The key architectural idea is mixed placement:

- the storefront remains on `N4A`
- only the assistant tier moves to `C4A`

This keeps the workshop focused on workload placement rather than on external retrieval infrastructure.
