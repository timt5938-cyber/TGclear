# Telegram Cleaner

Desktop application to identify and leave unwanted Telegram channels and group chats based on activity filters, with audit logging and best-effort rollback.

## Language

**Leave (Выход)**:
The physical departure from a channel or group chat via Telegram API (`LeaveChannel` / `DeleteChatUser`).
_Avoid_: Delete, erase, remove

**Rollback (Откат)**:
An operation that rejoins previously left public entities using their stored `@username`, reporting unrestorable private entities to the user.
_Avoid_: Full restore, undo

**Public Entity (Публичный канал/чат)**:
A channel or supergroup possessing a public `@username` that can be rejoined freely.
_Avoid_: Open chat

**Private Entity (Приватный канал/чат)**:
A channel or group lacking a public username, which cannot be automatically rejoined without an active invite link.
_Avoid_: Closed chat, hidden chat

**Direct Message (Личный диалог)**:
A one-on-one conversation with a human user or a bot, strictly excluded from modification or departure.
_Avoid_: Private message, PM, DM, 1-on-1 chat

**Protected Entity (Защищенная сущность)**:
A channel or group where the user possesses creator or administrator rights, which is pinned, or which is present in the Whitelist.
_Avoid_: Immune chat, safe chat

**Candidate Entity (Кандидат на выход)**:
A non-protected channel or group that satisfies active filter criteria for departure.
_Avoid_: Dead chat, garbage chat, target

**Whitelist (Белый список)**:
A user-defined set of channel and group identifiers explicitly shielded from departure regardless of filter results.
_Avoid_: Ignore list, exclusion list

**Filter Combination Mode (Режим комбинации фильтров)**:
The logical evaluation strategy (`ANY` / disjunctive OR vs `ALL` / conjunctive AND) determining whether a non-protected entity qualifies as a Candidate Entity.
_Avoid_: Matching rule, filter logic

**User Read Horizon (Горизонт прочтения)**:
The elapsed duration since the user last read incoming messages in the entity, inferred from the `read_inbox_max_id` timestamp and unread message count.
_Avoid_: User last seen, chat open time

**Dormancy Period (Период неактивности канала)**:
The elapsed duration since the most recent message was posted by any participant or broadcaster in the entity.
_Avoid_: Dead time, channel age

**Trigger Reason (Причина отбора)**:
The explicit diagnostic explanation displayed per candidate indicating which specific filter threshold was violated.
_Avoid_: Flag, status

**Local Web UI (Локальный веб-интерфейс)**:
The browser-based dashboard hosted locally on the machine to configure filters, inspect candidates, trigger leave operations, and execute rollbacks.
_Avoid_: Web app, website, desktop window

**Cleanup Batch (Сессия очистки)**:
A discrete execution run that departs from a set of confirmed candidate entities, logged with a timestamp and unique identifier.
_Avoid_: Task, job, deletion run

**Snapshot (Снапшот)**:
The persistent record of an entity's metadata (ID, title, username, trigger reason, leave timestamp, restorable status) captured immediately before departure.
_Avoid_: Backup file, entity dump

**Selective Rollback (Выборочный откат)**:
The targeted rejoining of individually picked public entities from a previous Cleanup Batch, rather than forcing an all-or-nothing restoration.
_Avoid_: Partial undo, pick restore

**Telegram Session (Сессия Telegram)**:
The persistent MTProto client credentials and authorization state (`.session` file) allowing subsequent runs without re-verifying phone codes.
_Avoid_: Auth token, login cache

**Auth Wizard (Мастер авторизации)**:
The multi-step browser form facilitating API ID/Hash entry, phone number verification, code delivery, and 2FA password verification.
_Avoid_: Login form, credentials dialog
