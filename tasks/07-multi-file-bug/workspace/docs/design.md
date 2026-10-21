# Cache design

## Expiry

Entries expire at `set_time + ttl`. An entry whose expiry has passed must not
be returned by `get`, and must not be counted by `size`.

## Sweeping

Expired entries are removed lazily on access. `size()` and `keys()` must not
report entries that have already expired — they sweep before answering, so
they are never a source of stale reads.

## Interaction with `reload()`

`session.reload()` re-reads every cached key. It must skip keys that have
expired, and must leave already-expired entries out of the cache afterwards.
