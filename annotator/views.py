import os
import io
import json
import glob
import xml.etree.ElementTree as ET
from pathlib import Path

import json as _json
from functools import wraps

from django.conf import settings
from django.http import JsonResponse, FileResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.shortcuts import render, redirect


def _login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('email'):
            return redirect(f'/login/?next={request.path}')
        return view_func(request, *args, **kwargs)
    wrapper.csrf_exempt = getattr(view_func, 'csrf_exempt', False)
    return wrapper


def _record_login(email, name):
    log_file = Path(settings.BASE_DIR) / 'login_history.json'
    try:
        records = _json.loads(log_file.read_text()) if log_file.exists() else []
    except Exception:
        records = []
    if any(r['email'] == email for r in records):
        return
    from datetime import datetime
    records.append({
        'email': email,
        'name': name,
        'first_seen': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
    log_file.write_text(_json.dumps(records, ensure_ascii=False, indent=2))


def login_view(request):
    error = ''
    next_url = request.GET.get('next', '/')
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        name = request.POST.get('name', '').strip()
        next_url = request.POST.get('next', '/')
        if email:
            display_name = name or email
            request.session['email'] = email
            request.session['name'] = display_name
            _record_login(email, display_name)
            return redirect(next_url or '/')
        else:
            error = 'Please enter a valid email.'
    return render(request, 'login.html', {'error': error, 'next': next_url})


def logout_view(request):
    request.session.flush()
    return redirect('/login/')


from .models import H3Cell, Annotation, CellResult

DATA_ROOT = settings.DATA_ROOT
YEARS = [2019, 2020, 2021, 2022, 2023, 2024]
RTS_COLORS = ['#3fb950', '#f0883e', '#58a6ff', '#ff7b72', '#d2a8ff', '#ffa657']


@_login_required
def index(request):
    return render(request, 'index.html')


def get_image_path(cell_id, prefix, year):
    pattern = os.path.join(DATA_ROOT, cell_id, f'{prefix}_{year}_id{cell_id}_*.png')
    files = glob.glob(pattern)
    return files[0] if files else None


@_login_required
def cell_list(request):
    email = request.session['email']
    page = int(request.GET.get('page', 1))
    page_size = 50
    status_filter = request.GET.get('status', '')

    if status_filter == 'pending':
        # 该用户未处理的：没有 CellResult 记录，或状态为 pending
        done_ids = CellResult.objects.filter(email=email).exclude(status='pending').values_list('cell_id', flat=True)
        qs = H3Cell.objects.exclude(id__in=done_ids)
    elif status_filter:
        cell_ids = CellResult.objects.filter(email=email, status=status_filter).values_list('cell_id', flat=True)
        qs = H3Cell.objects.filter(id__in=cell_ids)
    else:
        qs = H3Cell.objects.all()

    total = qs.count()
    cells = qs[(page - 1) * page_size: page * page_size]

    # 批量获取该用户对这些 cell 的状态
    cell_ids_page = [c.id for c in cells]
    user_statuses = {
        cr.cell_id: cr.status
        for cr in CellResult.objects.filter(email=email, cell_id__in=cell_ids_page)
    }

    return JsonResponse({
        'total': total,
        'page': page,
        'cells': [
            {
                'id': c.id,
                'cell_id': c.cell_id,
                'order': c.order_index,
                'status': user_statuses.get(c.id, 'pending'),
            }
            for c in cells
        ],
        'last_cell': request.session.get('last_cell'),
    })


@_login_required
def cell_detail(request, cell_id):
    email = request.session['email']
    try:
        cell = H3Cell.objects.get(cell_id=cell_id)
    except H3Cell.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)
    request.session['last_cell'] = cell_id

    # 该用户自己的标注
    annotations = {}
    for a in cell.annotations.filter(email=email):
        annotations[a.year] = a.polygon

    # 该用户自己的 result/status/note
    try:
        cr = CellResult.objects.get(cell=cell, email=email)
        user_result = cr.result
        user_status = cr.status
        user_note = cr.note
    except CellResult.DoesNotExist:
        user_result = ''
        user_status = 'pending'
        user_note = ''

    images = {}
    for year in YEARS:
        s2_path = get_image_path(cell_id, 's2', year)
        nir_path = get_image_path(cell_id, 's2nir', year)
        images[year] = {
            's2': f'/api/image/{cell_id}/s2/{year}/' if s2_path else None,
            'nir': f'/api/image/{cell_id}/s2nir/{year}/' if nir_path else None,
        }

    prev_cell = H3Cell.objects.filter(order_index__lt=cell.order_index).order_by('-order_index').first()
    next_cell = H3Cell.objects.filter(order_index__gt=cell.order_index).order_by('order_index').first()

    # 全局下一个未处理的：没有任何人标了 yes 或 no 的
    confirmed_ids = CellResult.objects.filter(result__in=['yes', 'no']).values_list('cell_id', flat=True).distinct()
    next_pending = H3Cell.objects.filter(order_index__gt=cell.order_index).exclude(id__in=confirmed_ids).order_by('order_index').first()

    return JsonResponse({
        'cell_id': cell_id,
        'order': cell.order_index,
        'status': user_status,
        'result': user_result,
        'note': user_note,
        'images': images,
        'annotations': annotations,
        'gif_url': f'/api/gif/{cell_id}/',
        'prev': prev_cell.cell_id if prev_cell else None,
        'next': next_cell.cell_id if next_cell else None,
        'next_pending': next_pending.cell_id if next_pending else None,
        'stats': _stats_dict(email),
    })


@_login_required
def serve_image(request, cell_id, prefix, year):
    path = get_image_path(cell_id, prefix, int(year))
    if not path:
        return HttpResponse(status=404)

    webp_path = path.replace('.png', '.webp')
    if not os.path.exists(webp_path):
        try:
            from PIL import Image
            img = Image.open(path).convert('RGB')
            img.save(webp_path, 'WEBP', quality=85, method=4)
        except Exception:
            response = FileResponse(open(path, 'rb'), content_type='image/png')
            response['Cache-Control'] = 'public, max-age=86400'
            return response

    response = FileResponse(open(webp_path, 'rb'), content_type='image/webp')
    response['Cache-Control'] = 'public, max-age=86400'
    return response


@_login_required
def serve_gif(request, cell_id):
    from PIL import Image
    frames = []
    size = (256, 256)
    for year in YEARS:
        path = get_image_path(cell_id, 's2', year)
        if path:
            img = Image.open(path).convert('RGB').resize(size, Image.LANCZOS)
        else:
            img = Image.new('RGB', size, (20, 20, 30))
        frames.append(img)
    if not frames:
        return HttpResponse(status=404)
    buf = io.BytesIO()
    frames[0].save(buf, format='GIF', save_all=True, append_images=frames[1:], duration=800, loop=0)
    buf.seek(0)
    return HttpResponse(buf, content_type='image/gif')


@_login_required
@require_http_methods(['GET'])
def hover_preview(request):
    cell_id = request.GET.get('cell_id')
    year = int(request.GET.get('year', 2019))
    x = int(request.GET.get('x', 0))
    y = int(request.GET.get('y', 0))
    prefix = request.GET.get('prefix', 's2')

    image_path = get_image_path(cell_id, prefix, year)
    if not image_path:
        return JsonResponse({'polygon': None})

    from .sam2_predictor import hover as sam2_hover
    polygon = sam2_hover(image_path, x, y)
    return JsonResponse({'polygon': polygon})


@_login_required
@csrf_exempt
@require_http_methods(['POST'])
def segment(request):
    data = json.loads(request.body)
    cell_id = data['cell_id']
    year = int(data['year'])
    points = data['points']
    labels = data['labels']
    prefix = data.get('prefix', 's2')
    rts_id = data.get('rts_id')
    hint_year = data.get('hint_year')

    image_path = get_image_path(cell_id, prefix, year)
    if not image_path:
        return JsonResponse({'error': 'image not found'}, status=404)

    store_key = (cell_id, rts_id, year) if rts_id else None
    hint_key = (cell_id, rts_id, int(hint_year)) if (rts_id and hint_year is not None) else None

    from .sam2_predictor import segment as sam2_segment
    polygon = sam2_segment(image_path, points, labels, hint_key=hint_key, store_key=store_key)
    if polygon is None:
        return JsonResponse({'error': 'segmentation failed'}, status=500)
    return JsonResponse({'polygon': polygon})


def _annotation_file_path(cell_id, email):
    prefix = email.split('@')[0] if '@' in email else email
    folder = Path(settings.BASE_DIR) / 'annotations' / cell_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{prefix}.json'


def _pixel_to_lonlat(col, row, gt):
    import math
    x = gt[0] + col * gt[1] + row * gt[2]
    y = gt[3] + col * gt[4] + row * gt[5]
    a = 6378137.0
    e = 0.0818191908426
    phi_c = math.radians(70)
    lam_0 = math.radians(-45)
    sin_phi_c = math.sin(phi_c)
    t_c = math.tan(math.pi / 4 - phi_c / 2) / ((1 - e * sin_phi_c) / (1 + e * sin_phi_c)) ** (e / 2)
    m_c = math.cos(phi_c) / math.sqrt(1 - e * e * sin_phi_c * sin_phi_c)
    rho = math.sqrt(x * x + y * y)
    if rho == 0:
        return [math.degrees(lam_0), 90.0]
    t = rho * t_c / (a * m_c)
    phi = math.pi / 2 - 2 * math.atan(t)
    for _ in range(10):
        s = math.sin(phi)
        phi = math.pi / 2 - 2 * math.atan(t * ((1 - e * s) / (1 + e * s)) ** (e / 2))
    lam = lam_0 + math.atan2(x, -y)
    return [round(math.degrees(lam), 7), round(math.degrees(phi), 7)]


def _build_geojson(cell_id, annotations):
    features = []
    for year_str, instances in annotations.items():
        year = int(year_str)
        gt = _read_aux_xml(cell_id, 's2', year)
        for inst in instances:
            polygon = inst.get('polygon')
            if not polygon:
                continue
            if gt:
                coords = [_pixel_to_lonlat(p[0], p[1], gt) for p in polygon]
                coords.append(coords[0])
            else:
                coords = [[p[0], p[1]] for p in polygon]
                coords.append(coords[0])
            features.append({
                'type': 'Feature',
                'properties': {'id': inst.get('id'), 'year': year, 'cell_id': cell_id},
                'geometry': {'type': 'Polygon', 'coordinates': [coords]},
            })
    return {'type': 'FeatureCollection', 'features': features}


@_login_required
@csrf_exempt
@require_http_methods(['POST'])
def save_annotation(request):
    data = json.loads(request.body)
    cell_id = data['cell_id']
    annotations = data['annotations']
    action = data.get('action', 'annotated')
    email = request.session.get('email', 'unknown')

    try:
        cell = H3Cell.objects.get(cell_id=cell_id)
    except H3Cell.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)

    # 只删除该用户对该 cell 的旧标注，再重写
    cell.annotations.filter(email=email).delete()
    for year_str, instances in annotations.items():
        if instances:
            Annotation.objects.create(cell=cell, email=email, year=int(year_str), polygon=instances)

    # 更新该用户自己的 result/status/note
    CellResult.objects.update_or_create(
        cell=cell,
        email=email,
        defaults={
            'result': data.get('result', ''),
            'note': data.get('note', ''),
            'status': action,
        },
    )

    # 更新 GeoJSON 文件
    ann_path = _annotation_file_path(cell_id, email)
    result_val = data.get('result', '')

    if not result_val:
        # result 已清空：删除该用户的 json 文件，文件夹空了就一起删
        if ann_path.exists():
            ann_path.unlink()
        folder = ann_path.parent
        if folder.exists() and not any(folder.iterdir()):
            folder.rmdir()
    else:
        geojson = _build_geojson(cell_id, annotations)
        geojson['validate_result'] = result_val
        ann_path.write_text(json.dumps(geojson, ensure_ascii=False, indent=2))

    request.session['last_cell'] = cell_id

    # 全局下一个未处理的（任何人都没标过的）
    globally_done_ids = CellResult.objects.exclude(status='pending').values_list('cell_id', flat=True).distinct()
    next_pending = H3Cell.objects.filter(order_index__gt=cell.order_index).exclude(id__in=globally_done_ids).order_by('order_index').first()
    next_cell = H3Cell.objects.filter(order_index__gt=cell.order_index).order_by('order_index').first()

    return JsonResponse({
        'success': True,
        'next': next_cell.cell_id if next_cell else None,
        'next_pending': next_pending.cell_id if next_pending else None,
        'stats': _stats_dict(email),
    })


def _stats_dict(email=None):
    total = H3Cell.objects.count()
    if email:
        qs = CellResult.objects.filter(email=email)
    else:
        qs = CellResult.objects.all()
    annotated = qs.filter(status='annotated').count()
    skipped = qs.filter(status='skipped').count()
    yes = qs.filter(result='yes').count()
    no = qs.filter(result='no').count()
    unknown = qs.filter(result='unknown').count()
    pending = total - qs.exclude(status='pending').count()
    return {
        'total': total,
        'annotated': annotated,
        'skipped': skipped,
        'pending': pending,
        'yes': yes,
        'no': no,
        'unknown': unknown,
    }


@_login_required
def stats(request):
    email = request.session['email']
    return JsonResponse(_stats_dict(email))


def _read_aux_xml(cell_id, prefix, year):
    png_path = get_image_path(cell_id, prefix, int(year))
    if not png_path:
        return None
    aux_path = png_path + '.aux.xml'
    if not os.path.exists(aux_path):
        return None
    try:
        tree = ET.parse(aux_path)
        gt_text = tree.getroot().find('GeoTransform').text
        return [float(v.strip()) for v in gt_text.split(',')]
    except Exception:
        return None


@_login_required
def georef(request, cell_id, prefix, year):
    gt = _read_aux_xml(cell_id, prefix, year)
    if gt is None:
        return JsonResponse({'error': 'aux.xml not found'}, status=404)
    return JsonResponse({'geotransform': gt, 'crs': 'EPSG:3413'})
