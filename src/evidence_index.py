"""把已解析的文档切成可按来源定位的证据块（暂不做检索）。"""

from dataclasses import dataclass
import re
from typing import Sequence

from document_loader import LoadedDocument


@dataclass(frozen=True)
class EvidenceChunk:
    chunk_id: str
    source: str
    page: int | None
    text: str


def build_evidence_index(
    documents: Sequence[LoadedDocument], chunk_size: int = 500
) -> list[EvidenceChunk]:
    """先按空行分段；长段按字符切开，保留文件名和 PDF 页码。"""
    if chunk_size < 1:
        raise ValueError("chunk_size 必须大于 0")

    chunks: list[EvidenceChunk] = []
    for document_number, document in enumerate(documents, start=1):
        for segment_number, segment in enumerate(document.segments, start=1):
            chunk_number = 0
            for paragraph in re.split(r"\n\s*\n", segment.text):
                paragraph = paragraph.strip()
                if not paragraph:
                    continue

                for start in range(0, len(paragraph), chunk_size):
                    chunk_number += 1
                    chunks.append(
                        EvidenceChunk(
                            chunk_id=f"d{document_number}-s{segment_number}-c{chunk_number}",
                            source=document.source,
                            page=segment.page,
                            text=paragraph[start : start + chunk_size],
                        )
                    )

    return chunks
