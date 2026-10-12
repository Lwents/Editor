"""Optional VietOCR line recognizer; configuration from pbcquoc/vietocr."""
from functools import lru_cache
from app.core import BACKEND_DIR


@lru_cache(maxsize=1)
def get_predictor():
    import torch
    import requests
    from vietocr.tool.predictor import Predictor

    weights = BACKEND_DIR / 'models' / 'ocr' / 'vgg_seq2seq.pth'
    weights.parent.mkdir(parents=True, exist_ok=True)
    if not weights.is_file():
        pending = weights.with_suffix('.pending')
        try:
            with requests.get('https://vocr.vn/data/vietocr/vgg_seq2seq.pth', stream=True, timeout=(15, 120)) as response:
                response.raise_for_status()
                with pending.open('wb') as target:
                    for chunk in response.iter_content(1024 * 1024):
                        target.write(chunk)
            pending.replace(weights)
        finally:
            pending.unlink(missing_ok=True)
    torch.set_num_threads(4)
    import json
    from pathlib import Path
    config = json.loads(Path(__file__).with_name('vietocr-config.json').read_text(encoding='utf-8'))
    config.update(device='cpu', weights=str(weights))
    config['cnn']['pretrained'] = False
    config['predictor']['beamsearch'] = False
    return Predictor(config)
