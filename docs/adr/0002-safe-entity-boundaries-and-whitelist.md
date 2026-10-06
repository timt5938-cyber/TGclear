# Safe Entity Boundaries and Whitelisting

Direct personal messages (including bots) and chats where the user holds administrator or creator privileges carry critical private data and administrative authority. We decided to strictly exclude all Direct Messages from scanning, and to automatically classify admin/creator and pinned chats as Protected Entities. An explicit Whitelist allows users to exempt specific channels or groups from departure irrespective of filter thresholds.
