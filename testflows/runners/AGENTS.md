# Runners Core AGENTS.md

- The `CloudProvider` abstraction includes extensive recycling methods (e.g., `claim_recycled_server`) specifically to support Scaleway SBS mode; avoid adding more specialized provider logic to this base class to prevent interface bloat.
- `scale_up.py` uses a dual-layered fallback mechanism (Server Type $\rightarrow$ Provider) and implements "retry via scale-down" by proactively deleting recyclable servers when creation fails due to resource contention.
- `scale_down.py` handles three distinct resource cleanup states: recycling stopped servers, detecting zombie runners on existing VMs, and removing unused active VMs without corresponding GitHub jobs.
