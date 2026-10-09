"""assemble 子模块：拼装与后处理。"""
from .fold_sections import build_fold_sections
from .image_utils import copy_images, check_image_links, fix_image_links
from .headings import demote_headings
from .source_note import render_source_notes

__all__ = [
    "build_fold_sections",
    "copy_images",
    "check_image_links",
    "fix_image_links",
    "demote_headings",
    "render_source_notes",
]
