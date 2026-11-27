"""등록한 물체 다시 알아보기 (재인식).

- augment.py : 잘라내기 전처리 + 사진 1장 → 증강 여러 장 (OpenCV 만 사용)
- embedder.py: 생김새 특징값 (DINOv2-small 기본 / CLIP ViT-B/32)
- gallery.py : 물체별 특징값 묶음(갤러리) 저장·비교·판정
- tracks.py  : 지금 냉장고 안 물체 목록(트랙) + 변화 영역 + 갱신 규칙

설정은 config.py 의 '4-5'. vision.YoloComparer 가 REID_MODE="gallery" 일 때 사용합니다.
"""
