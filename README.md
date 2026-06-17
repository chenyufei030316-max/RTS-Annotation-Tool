# RTS Annotation Tool

A web-based annotation tool for mapping Retrogressive Thaw Slumps (RTS) from multi-year Sentinel-2 imagery, powered by SAM2.

## Installation

1. pip install -r requirements.txt
2. Install SAM2: https://github.com/facebookresearch/sam2
3. Edit DATA_ROOT and SAM2_CHECKPOINT in rts_annotator/settings.py
4. python manage.py migrate
5. python manage.py import_cells
6. python manage.py runserver 0.0.0.0:8000
