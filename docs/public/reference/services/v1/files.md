# Files

`get`, `list`, `all`, `iter`, uploads and downloads use V1: only V1 has a file's person,
company or opportunity, and it works under any API version pin. `get_download_url` uses V2 when
it can (one request for the details and a signed URL) and V1 otherwise. `feed`/`iter_feed`
list every file visible to you, newest first, through V2 (Affinity API 2026-09-17+).

::: affinity.services.v1_only.EntityFileService
