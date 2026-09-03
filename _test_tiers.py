from winocr.services.ocr.rapidocr import RapidOcrEngine
from PIL import Image

image = Image.open('_case1_src.png').convert('RGB')

for tier in ['tiny', 'small', 'medium']:
    print('========', tier, '========')
    for trial in range(2):
        e = RapidOcrEngine()
        e.structured = False
        e.preprocess = True
        e.model_type = tier
        e._engine = None
        e._models_cache = {}
        res = e.recognize(image)
        print(' Trial', trial, ': lines=', len(res.lines), 'conf=', round(res.confidence, 3))
        for i, l in enumerate(res.lines):
            print('   L', i, ':', l[:50])
