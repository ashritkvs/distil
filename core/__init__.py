"""TraceFlow Compress — core compression + metrics library."""

from core.compressor import CompressionResult, compress
from core.intelligence import analyze, count_tokens

__all__ = ["compress", "CompressionResult", "analyze", "count_tokens"]
