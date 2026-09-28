import re
from typing import List

from evidence_index import EvidenceChunk

#用于切割固定chunk中的句子
def _split_sentences( text: str) -> List[str]:
        sentences = re.split(r'[。！？.!?\n]+', text)
        return [s.strip() for s in sentences if s.strip()]

def keyword_search(
    chunks: list[EvidenceChunk],
    keywords: list[str],
    top_k: int = 3,
) :


    sorted_chunks = []
    if  keywords == None:
        raise ValueError("当前keywords列表为None!")

    for chunk in chunks:
        text = chunk.text
        chunk_id = chunk.chunk_id
        chunk_source = chunk.source
        chunk_page = chunk.page

        matches = []
        total_score = 0

        for keyword in keywords:
            keyword_lower = keyword.casefold()
            count = text.casefold().count(keyword_lower)
            if count > 0:
                matches.append(keyword)
                total_score += count * len(keyword_lower)

        if total_score > 0:
            sentences = _split_sentences(text)
            matched_sentences = []

            for sentence in sentences:
                if any(keyword.casefold() in sentence.casefold() for keyword in matches):
                     matched_sentences.append(sentence)

            sorted_chunks.append({
                 "chunk_id":chunk_id,
                 "chunk_source":chunk_source,
                 "chunk_page":chunk_page,
                 "score" : total_score,
                 "matched_sentences":matched_sentences[:5],
                 "key_words_found":matches}
            )
    #排列topk个chunk
    top_chunks = sorted(
    sorted_chunks,
    key=lambda item: item["score"],
    reverse=True,
    )[:top_k]


    if not top_chunks:
         tool_result = f"没有与关键词{keywords}匹配的片段"
         tool_log = {"chunks_found":0}
         return {
         "tool_result":tool_result,
         "tool_log": tool_log
    }

    result_items = []
    for item in top_chunks:
         if item['matched_sentences']:
              matched_text = "... " + " ... ".join(item['matched_sentences']) + " ..."
         else:
            matched_text = "没有匹配到完整句子"
         result_items.append(f"Chunk ID: {item['chunk_id']}, Matched keywords in chunk: {matched_text}")

    tool_result = "\n\n".join(result_items)



    tool_log = {"chunks_found": len(top_chunks)}
    return {
         "tool_result":tool_result,
         "tool_log": tool_log,
    }
