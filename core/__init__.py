"""core 包：论文转写核心逻辑。

全流程：ingest → parse(mineru) → normalize → plan → rewrite → assemble

主要入口：
    from core import transform_paper
    result = transform_paper("path/to/paper.pdf", fake=True)
"""
from .pipeline import transform_paper, PaperResult, ProgressCallback
from .errors import ETError, InputError, ServiceError

__all__ = [
    "transform_paper",
    "PaperResult",
    "ProgressCallback",
    "ETError",
    "InputError",
    "ServiceError",
]
