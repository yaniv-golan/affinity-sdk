# Interactions

`list`, `get` and the writes (`create`, `update`, `delete`) use V1. The per-type reads
(`list_emails`, `list_meetings`, `get_meeting`, `list_calls`, `list_chat_messages` and their
`iter_*` forms) use V2.

::: affinity.services.v1_only.InteractionService
