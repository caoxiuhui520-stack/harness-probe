"""Word-frequency helper used by the search page."""

import re

_WORD = re.compile(r"[a-z']+")


def tokenize(text):
    """Split text into lowercase word tokens."""
    return _WORD.findall(text.lower())


def word_counts(text):
    """Count how many times each word appears in ``text``."""
    counts = {}
    for word in tokenize(text):
        counts[word] = counts.get(word, 0) + 1
    return counts


def top_words(text, n=10):
    """Return the ``n`` most common words as (word, count), highest first."""
    counts = word_counts(text)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]
