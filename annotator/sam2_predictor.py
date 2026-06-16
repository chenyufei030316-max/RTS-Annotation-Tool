import threading
import queue
import numpy as np
import cv2
import torch
from django.conf import settings

POOL_SIZE = 2

_hint_cache = {}
_hint_lock = threading.Lock()

_pool = None
_pool_lock = threading.Lock()


def _build_predictor():
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model = build_sam2(settings.SAM2_CONFIG, settings.SAM2_CHECKPOINT, device=device)
    return SAM2ImagePredictor(model), device


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                q = queue.Queue()
                for _ in range(POOL_SIZE):
                    predictor, device = _build_predictor()
                    q.put({'predictor': predictor, 'device': device, 'image_key': None})
                _pool = q
    return _pool


class _PoolWorker:
    """从池里借一个 predictor，用完自动归还。"""
    def __init__(self, block=True):
        self._block = block
        self._slot = None

    def __enter__(self):
        pool = _get_pool()
        try:
            self._slot = pool.get(block=self._block, timeout=0 if not self._block else None)
        except queue.Empty:
            self._slot = None
        return self._slot

    def __exit__(self, *_):
        if self._slot is not None:
            _get_pool().put(self._slot)


def hover(image_path, x, y):
    image = cv2.imread(image_path)
    if image is None:
        return None
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    with _PoolWorker(block=False) as slot:
        if slot is None:
            return None  # 所有实例都在忙，跳过 hover
        try:
            predictor, device = slot['predictor'], slot['device']
            if slot['image_key'] != image_path:
                with torch.inference_mode():
                    with torch.autocast(device_type=device, dtype=torch.bfloat16):
                        predictor.set_image(image_rgb)
                slot['image_key'] = image_path
            with torch.inference_mode():
                with torch.autocast(device_type=device, dtype=torch.bfloat16):
                    masks, scores, _ = predictor.predict(
                        point_coords=np.array([[x, y]], dtype=np.float32),
                        point_labels=np.array([1], dtype=np.int32),
                        multimask_output=True,
                    )
            best = int(np.argmax(scores))
            return mask_to_polygon(masks[best] > 0.5)
        except Exception:
            slot['image_key'] = None
            return None


def segment(image_path, points, labels, hint_key=None, store_key=None):
    image = cv2.imread(image_path)
    if image is None:
        return None
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    point_coords = np.array(points, dtype=np.float32)
    point_labels = np.array(labels, dtype=np.int32)

    with _hint_lock:
        sam_mask_input = _hint_cache.get(hint_key) if hint_key else None

    with _PoolWorker(block=True) as slot:
        try:
            predictor, device = slot['predictor'], slot['device']
            if slot['image_key'] != image_path:
                with torch.inference_mode():
                    with torch.autocast(device_type=device, dtype=torch.bfloat16):
                        predictor.set_image(image_rgb)
                slot['image_key'] = image_path
            with torch.inference_mode():
                with torch.autocast(device_type=device, dtype=torch.bfloat16):
                    masks, scores, low_res = predictor.predict(
                        point_coords=point_coords,
                        point_labels=point_labels,
                        mask_input=sam_mask_input,
                        multimask_output=True,
                    )
        except Exception:
            slot['image_key'] = None
            return None

    best = int(np.argmax(scores))
    if store_key:
        with _hint_lock:
            _hint_cache[store_key] = low_res[best:best + 1].astype(np.float32)
    return mask_to_polygon(masks[best] > 0.5)


def mask_to_polygon(mask):
    mask_uint8 = mask.astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    epsilon = 0.002 * cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, epsilon, True)
    return approx.reshape(-1, 2).tolist()
