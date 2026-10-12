# Notes

Reads by entity (`list`, `iter`) use V1; `get`, `feed`, `iter_feed`, `list_replies`, `search` and the
writes (`create`, `update`, `delete`) use V2, which stores notes as HTML.

::: affinity.services.v1_only.NoteService
