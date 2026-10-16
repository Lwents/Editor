"""Bounded source-evidence recovery for failed cues, preserving original keys."""
import asyncio
import json
from collections import Counter
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

from app.services.persistence import atomic_json, read_json
from .caption_evidence import NON_CAPTION
from .cue_recovery import CueRecovery

repair_source = ContextVar('translation_repair_source', default=None)


async def repair_failed_cues(batch, *, context, memory, previous, following, timeout=120):
    from .web_provider import browser, is_web_engine
    from .translation import _translate_gemini_batch, TranslationBatchStructureError, _compact_context
    from app.services.subtitles.timing import SubtitleEvent
    from app.services.subtitles.vision_review import _frame_sheet, _local_consensus
    configured = repair_source.get()
    if not configured or not is_web_engine():
        return {}, {}
    source, directory, allow_images = configured
    if not Path(source).is_file():
        return {}, {}
    directory = Path(directory)
    texts, updates = {}, {}
    from app.services.subtitles.timing import parse_srt
    grouped = directory / 'subtitles.grouped.srt'
    all_events = parse_srt(grouped) if grouped.is_file() else []
    report = read_json(directory / 'subtitles.recognition.json') or {}
    for offset in range(0, len(batch), 4):
        chunk = batch[offset:offset+4]
        events = [SubtitleEvent(*item['time'], str(item['source'])) for item in chunk]
        local_ids = list(range(1, len(events)+1))
        if not allow_images:
            # Respect text-only mode: images remain local and only validated OCR is sent.
            decisions = await asyncio.to_thread(_local_consensus, Path(source), events, local_ids, report.get('regions'), [])
            verified = {item['id']:item['text'] for item in decisions if item.get('is_caption') and item.get('confidence',0)>=.85}
            corrected = [dict(item, source=verified[i+1]) for i,item in enumerate(chunk) if i+1 in verified]
            if not corrected:
                continue
            from .translation import create_translation_client
            try:
                result, changed = await _translate_gemini_batch(create_translation_client(), corrected,'zh','vi',context,memory,previous,following,request_timeout_seconds=timeout)
                texts.update({int(item['id']):text for item,text in zip(corrected,result)})
                updates.update(changed)
            except TranslationBatchStructureError as exc:
                texts.update(exc.valid_items)
            continue
        sheet = directory / 'caption-repair' / ('-'.join(str(i['id']) for i in chunk)+'.jpg')
        # Three samples distinguish persistent background text from a short
        # caption that is absent only at the midpoint. Keep original cue IDs.
        samples = []
        for event in events:
            duration = event.end - event.start
            for fraction in (.15, .5, .85):
                at = event.start + duration * fraction
                samples.append(SubtitleEvent(at, at, event.text))
        frame_ids = list(range(1, len(samples) + 1))
        await asyncio.to_thread(_frame_sheet, Path(source), samples, frame_ids, sheet, report.get('regions'), False, True)
        prompt = ('Đọc phụ đề tiếng Trung thực sự nhìn thấy trong từng ô ảnh rồi dịch sang tiếng Việt. '
            'OCR trước đó bị lẫn chữ nền/watermark/ký tự sai; chỉ loại chữ đó khi ảnh xác nhận. '
            'Dùng source_neighbours để hiểu câu bị ngắt, nhưng không dịch lấn nội dung câu kế tiếp. Không đoán phần không đọc được, không lặp chữ Trung vào bản dịch. Giữ tên và xưng hô theo ngữ cảnh. '
            'Câu bị ngắt giữa chừng phải dựa câu sau để chọn nghĩa, không ép thành câu trọn vẹn hoặc tự bịa phần còn thiếu; có thể giữ dấu … ở chỗ ngắt. '
            'Ảnh dùng local_id, nhưng kết quả items dùng id gốc trong current_items. '
            'Mỗi câu có 3 ảnh local_ids theo thời gian. Phân biệt phụ đề/lời bình chèn trên video với chữ nằm trên đồ vật, ruy băng, quần áo, logo và watermark. '
            'Mỗi item gồm id, text (bản dịch), source_verified (chữ nguồn thực sự nhìn thấy), is_caption (boolean), confidence (0..1), reason. '
            'Kiểm tra cả full frame và caption band, vì phụ đề có thể nằm ngoài vùng cắt. CHỈ khi cả 3 ảnh đều chắc chắn không có phụ đề, trả is_caption=false, text=__NON_CAPTION__, source_verified="", non_caption_frame_ids bằng đủ 3 local_ids đã xem, và nêu reason. '
            'Nếu bất kỳ ảnh nào có phụ đề hoặc không đọc chắc, không kết luận không có phụ đề; giữ is_caption=true và confidence thấp nếu chưa đọc được. '
            'Nếu không đọc rõ, confidence thấp; không bịa bản dịch. memory_updates={}. Chỉ trả JSON.\n'
            + json.dumps({'current_items':[dict(item,local_ids=list(range(i*3+1,i*3+4)),source_neighbours=[{'id':j+1,'source':all_events[j].text} for j in range(max(0,int(item['id'])-3),min(len(all_events),int(item['id'])+2)) if j+1 != int(item['id'])]) for i,item in enumerate(chunk)],'context':_compact_context(context),
                'previous_context':previous,'next_context':following},ensure_ascii=False))
        data=json.loads(await browser.complete([{'role':'user','content':prompt}], timeout=timeout, attachments=[sheet]))
        entries=[item for item in data.get('items',[]) if isinstance(item,dict) and type(item.get('id')) is int]
        counts=Counter(item['id'] for item in entries)
        by_id={item['id']:item for item in entries}
        rejected = {}
        for i, item in enumerate(chunk):
            decision = by_id.get(item['id'], {})
            confidence = decision.get('confidence')
            if (counts[item['id']] == 1 and decision.get('is_caption') is False
                    and type(confidence) in (int, float) and .95 <= confidence <= 1
                    and decision.get('non_caption_frame_ids') == list(range(i*3+1,i*3+4))
                    and isinstance(decision.get('reason'), str) and decision['reason'].strip()
                    and decision.get('source_verified') == ''):
                rejected[str(item['id'])] = dict(decision, input_key=CueRecovery.key(item))
        if rejected:
            evidence = directory / 'caption-repair' / 'decisions.json'
            stored = read_json(evidence) or {}
            stored.update(rejected)
            atomic_json(evidence, stored)
            texts.update({int(i): NON_CAPTION for i in rejected})
        accepted=[item for item in chunk if by_id.get(item['id'], {}).get('is_caption') is not False and counts[item['id']]==1 and type(by_id.get(item['id'],{}).get('confidence')) in (int,float)
            and .85 <= by_id[item['id']]['confidence'] <= 1
            and isinstance(by_id[item['id']].get('source_verified'),str)
            and by_id[item['id']]['source_verified'].strip()]
        if not accepted:
            continue
        selected={'items':[by_id[item['id']] for item in accepted], 'memory_updates':{}}
        async def reply(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(selected,ensure_ascii=False)))])
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=reply)))
        try:
            result, _ = await _translate_gemini_batch(client,accepted,'zh','vi',context,memory,previous,following)
            valid={int(item['id']):text for item,text in zip(accepted,result)}
        except TranslationBatchStructureError as exc:
            valid=exc.valid_items
        texts.update(valid)
        # Keep image decisions for review; never rewrite the original source/checkpoint fingerprint.
        evidence=directory/'caption-repair'/'decisions.json'
        stored=read_json(evidence) or {}
        stored.update({str(i):by_id[i] for i in valid})
        atomic_json(evidence,stored)
    return texts, updates
