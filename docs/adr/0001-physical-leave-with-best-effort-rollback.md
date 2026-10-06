# Physical Leave with Best-Effort Rollback

Telegram does not support true "undo" for leaving conversations, and private channels cannot be rejoined via API without an admin invite link. We decided to perform actual physical departure (`LeaveChannel`) rather than soft-archiving, storing full channel metadata locally prior to exit. Rollback will re-subscribe to public channels via `@username` with rate-limit pacing, while presenting an unrecoverable report for private channels.
