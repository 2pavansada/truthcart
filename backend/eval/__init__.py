"""TruthCart evaluation framework (M-10).

Independent, deterministic, local-only evaluation of the runtime against the
specification (``GROUNDING_AND_REFUSAL.md`` decision rules D-01..D-17 and the
response/grounding contract in ``CONTRACTS.md`` / ``EVALUATION_PLAN.md``).

Components (see ``EVALUATION_PLAN.md`` §3/§7 and ``folder_structure.md``):
  * ``dataset``    -- EvalCase / ExpectedBehaviour adapter + JSON loader
  * ``auditor``    -- independent post-hoc audit of final ``ChatResponse``
                      objects against the trusted SQLite catalogue
                      (imports nothing from app.generation/decision/validation)
  * ``runner``     -- load cases, drive them through ``Orchestrator.handle``,
                      audit, compute gates, emit report + process exit status
  * ``metrics``    -- provisional aggregate metrics (never merged with official)
  * ``report``     -- machine-readable JSON + concise human Markdown
"""
