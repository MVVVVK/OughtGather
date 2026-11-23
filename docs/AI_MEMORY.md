# Editorial memory

The encrypted feedback ledger belongs to OughtGather, not the model. Only the locally compiled profile is provided to the shadow model; the original feedback is retained in the encrypted ledger, not the report. Keep private feedback out of public workflow inputs, commits, issues, and logs.

Create a 32-byte random key encoded in base64 and save it as repository secret `AI_MEMORY_KEY`. It must be independent of the DeepSeek key and delivery backup key. Losing this key makes the encrypted ledger and its backups unreadable; store a private offline copy. In Jianguoyun create `OughtGatherState` yourself; optionally set repository variable `AI_MEMORY_REMOTE_DIR` to another dedicated existing directory. WebDAV URL, username, and password use the existing secrets. This directory is separate from the EPUB delivery directory.

Ask a trusted assistant to turn natural-language editorial feedback into a patch and review it yourself. Example fictional patch:

```json
{"schema_version":1,"patch_id":"fictional-2026-01","operation":"add","entry":{"kind":"preference","strength":"soft","instruction":"Prefer reporting with historical context","scope":["reporting_style"],"signals":{"prefer":["historical_context"],"deprioritize":[]},"reference_articles":[],"original_feedback":"I value an explanation of the historical context."}}
```

GitHub Actions only exposes a new `workflow_dispatch` workflow in the normal Actions UI after the workflow file is present on the repository default branch. This is a GitHub platform behavior, not a project failure: PR #6 can be reviewed and merged with the default `AI_EDITOR_MODE=off`, then the Memory WebDAV online validation can be performed. Do not enable AI merely to merge.

After that, save the JSON privately as the `AI_MEMORY_PATCH` secret and run **Apply Editorial Memory Patch**. Use `validate_only` first; uncheck it and check `authorize_apply` to apply. Reusing a patch ID is idempotent. For local operation, export `AI_MEMORY_KEY` and the WebDAV credentials in the environment and use `python -m src.ai.memory validate patch.json` or `python -m src.ai.memory apply patch.json`. Set `WEBDAV_ENABLED=true`; do not put keys in command-line arguments.

To correct or retract, use a new patch ID and `operation` of `supersede` with `target_id` and a replacement entry, or `deactivate` with `target_id`. History remains in the encrypted ledger. A verified `editorial-memory.previous.enc` is retained before updating the current file. If recovery is necessary, stop competing updates and use the WebDAV interface to restore that copy to `editorial-memory.enc`; verify with the original key before resuming. A corrupted current file is never silently replaced with an empty ledger. Do not run multiple independent writers concurrently; the GitHub workflow serializes itself but cannot lock external clients.
