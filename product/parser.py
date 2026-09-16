"""Bounded original-text extraction in a child process; no OCR or execution."""
import hashlib
import json
from pathlib import Path
import re
import sys

PARSER_VERSION = 'pymupdf-1.27.2.2-text-3'
MAX_PAGES, MAX_CHARS = 100, 2_000_000


def extract(path, media):
    pages = []
    if media == 'application/pdf':
        import fitz
        with fitz.open(path) as document:
            if not document.is_pdf or document.needs_pass:
                raise ValueError('pdf_locked_or_invalid')
            if document.page_count > MAX_PAGES:
                raise ValueError('too_many_pages')
            size = 0
            for index, page in enumerate(document):
                text = page.get_text(sort=False)
                size += len(text)
                if size > MAX_CHARS:
                    raise ValueError('extracted_text_too_large')
                pages.append({'page': index + 1, 'text': text})
    else:
        text = Path(path).read_text(encoding='utf-8-sig')
        if '\x00' in text:
            raise ValueError('invalid_text_encoding')
        if len(text) > MAX_CHARS:
            raise ValueError('extracted_text_too_large')
        pages = [{'page': 1, 'text': text}]
    if not any(p['text'].strip() for p in pages):
        raise ValueError('no_extractable_text')
    chunks = []
    for page in pages:
        # Offset ranges refer to the persisted extracted page text, without normalization.
        for paragraph, match in enumerate(re.finditer(r'\S(?:.*?\S)?(?=\n\s*\n|\s*\Z)', page['text'], re.S), 1):
            for start in range(match.start(), match.end(), 1100):
                end = min(start + 1400, match.end())
                text = page['text'][start:end]
                chunks.append({'page': page['page'], 'paragraph': paragraph, 'char_start': start, 'char_end': end,
                               'text': text, 'content_sha256': hashlib.sha256(text.encode()).hexdigest()})
                if end == match.end():
                    break
    return {'parser_version': PARSER_VERSION, 'pages': pages, 'chunks': chunks}


def main():
    if sys.platform != 'win32':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    try:
        result = extract(sys.argv[1], sys.argv[2])
    except Exception as error:
        known = {'pdf_locked_or_invalid','too_many_pages','extracted_text_too_large','invalid_text_encoding','no_extractable_text'}
        result = {'error_code': str(error) if isinstance(error, ValueError) and str(error) in known else 'parse_failed'}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode('utf-8'))


if __name__ == '__main__':
    main()
