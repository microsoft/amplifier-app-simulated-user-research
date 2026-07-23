---
bundle:
  name: simulated-user-research
  version: 0.1.0
  description: >
    Agent-callable tools for the simulated-user-research attractor pipeline:
    run_research_round (drives a full research round against a web product)
    and research_doctor (environment diagnostics). Thin wrapper over the
    amplifier_simulated_user_research lib -- pipelines/simulated-user-research.dot
    remains the pipeline's actual logic home; this bundle never reimplements it.

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
    config:
      default_model: claude-sonnet-4-6

session:
  orchestrator:
    module: loop-agent
    source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main

tools:
  - module: tool-simulated-user-research
    source: ./modules/tool-simulated-user-research
---

# simulated-user-research

@context/simulated-user-research-awareness.md
