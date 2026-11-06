Notion Sync keeps its working files here. Anki preserves this folder when
the add-on is upgraded.

cache-<profile>-<id>.json  Row cache, one file per Anki profile: which Notion
                           rows were already downloaded, what was written to
                           their cards, and what the last sync couldn't read.
                           Safe to delete: the next sync then downloads every
                           row again.
.env                       Optional: NOTION_API_TOKEN=... if you prefer not to
                           store the token in the add-on config.
