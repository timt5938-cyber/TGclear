# Persistent Audit Snapshots and Selective Rollback

Bulk departure operations can inadvertently remove channels that a user later needs. We decided to persist full metadata snapshots of each Cleanup Batch in a local SQLite database prior to issuing any API leave calls. The history view allows users to review past batches and execute either batch-wide or selective single-entity rollbacks with real-time rate limit throttling.
