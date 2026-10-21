# Release-note policy

An entry is **user-visible** when a person using the product would notice the
change. Everything else is internal and must not appear in a release note.

## Rules

1. `Added` and `Fixed` entries are user-visible.
2. An entry whose text begins with `internal:` is **never** user-visible,
   whatever its kind — the prefix marks a change with no observable effect.
   This rule overrides rule 1.
3. `Internal` entries are never user-visible, whatever their text.
4. An entry whose text begins with `chore:` is never user-visible, whatever
   its kind. This rule overrides rules 1, 2 and 3.
5. Any other kind is user-visible unless rule 2, 3 or 4 applies.

## Ordering

Sections appear in the order Added, Changed, Fixed. Entries keep the order they
appeared in the source fragments.

## Fragments

`collect_entries` reads fragments in the order given. A missing file is an
error, not something to skip silently.
