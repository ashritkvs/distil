"""Prompt intelligence: tokenization, filler detection, redundancy analysis.

Reuses the filler list and tiktoken counting approach from the Prompt
Compression Agent. Pure Python — no ML model, serverless-friendly.
"""

from __future__ import annotations

import re

import tiktoken

# Shared encoder — cl100k_base closely approximates modern GPT/Claude
# tokenization. (For exact gpt-4o-family counts one would use o200k_base;
# cl100k is a close, well-understood approximation.)
_enc = tiktoken.get_encoding("cl100k_base")

# Multi-word filler / politeness phrases (reused + extended from the agent).
FILLERS = [
    "at your earliest convenience",
    "as much as possible",
    "i was wondering if you could",
    "i was wondering",
    "i would like you to",
    "i would like to",
    "i just wanted to",
    "could you please",
    "if it is at all possible",
    "if at all possible",
    "if possible",
    "please could you",
    "could you",
    "would you kindly",
    "do not hesitate",
    "feel free to",
    "feel free",
    "always remember to",
    "always remember",
    "never forget to",
    "never forget",
    "detailed and comprehensive",
    "detailed and",
    "very detailed",
    "in a very",
    "kindly",
    "please",
    "maybe",
    "possibly",
    "actually",
    "really",
    "just",
    "very",
    "basically",
    "literally",
    "absolutely",
    "comprehensive",
    "thorough",
]

# Vague, low-information content words that survive stopword filtering but add
# little meaning. Scored low so compression drops them early.
VAGUE_WORDS = {
    "way", "ways", "thing", "things", "stuff", "manner", "kind", "sort",
    "lot", "lots", "bit", "aspect", "aspects", "part", "parts", "area",
    "really", "actually", "basically", "literally", "simply", "essentially",
    "certain", "various", "several",
}

# Regex filler patterns for verbose constructions that literal matching misses,
# e.g. "in a very detailed and comprehensive way".
FILLER_PATTERNS = [
    r"\bin a\b(?:\s+\w+){0,4}?\s+\b(?:way|manner|fashion)\b",
    r"\bas\s+\w+\s+as\s+possible\b",
    r"\bfrom\s+start\s+to\s+finish\b",
    r"\bstep\s+by\s+step\b",
    r"\bonce\s+and\s+for\s+all\b",
]

# Common English stopwords / low-information function words. Removing these is
# safe-ish for compression; content words are preserved by scoring.
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "for", "with", "as", "by", "from", "into", "about", "is", "are", "was",
    "were", "be", "been", "being", "am", "do", "does", "did", "that", "this",
    "these", "those", "it", "its", "i", "you", "he", "she", "they", "we", "me",
    "my", "your", "so", "than", "then", "there", "here", "which", "who", "whom",
    "what", "how", "all", "any", "some", "each", "both", "few", "more", "most",
    "up", "out", "over", "under", "again", "can", "will", "would", "could",
    "should", "may", "might", "must", "have", "has", "had",
}

_WORD_RE = re.compile(r"\S+")


def count_tokens(text: str) -> int:
    """Exact token count using tiktoken cl100k_base."""
    return len(_enc.encode(text))


def find_fillers(text: str) -> list[str]:
    """Return the filler phrases present in `text` (case-insensitive)."""
    lower = text.lower()
    found = []
    for f in FILLERS:
        if re.search(r"\b" + re.escape(f) + r"\b", lower):
            found.append(f)
    return found


def redundancy_pct(text: str) -> float:
    """Estimate redundancy as the share of tokens that are duplicate or filler.

    Combines lexical repetition (repeated word forms) with filler density,
    capped at a sane ceiling. Real, deterministic — not model-based.
    """
    words = [w.lower().strip(".,!?;:\"'()") for w in _WORD_RE.findall(text)]
    words = [w for w in words if w]
    if not words:
        return 0.0
    unique = len(set(words))
    repetition = 1.0 - (unique / len(words))  # 0 = all unique
    filler_hits = sum(1 for w in words if w in {f for f in FILLERS if " " not in f})
    filler_density = filler_hits / len(words)
    score = (repetition * 0.5 + filler_density * 0.5) * 100
    return round(min(70.0, score + 5.0), 1)


def analyze(text: str) -> dict:
    """Full prompt-intelligence report (no compression)."""
    tokens = count_tokens(text)
    words = _WORD_RE.findall(text)
    return {
        "tokens": tokens,
        "words": len(words),
        "fillers": find_fillers(text),
        "redundancy_pct": redundancy_pct(text),
    }
