import os
from django.core.management.base import BaseCommand
from django.conf import settings
from annotator.models import H3Cell


class Command(BaseCommand):
    help = '导入H3格网单元到数据库'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None, help='只导入前N个格网')

    def handle(self, *args, **options):
        data_root = settings.DATA_ROOT
        cell_ids = sorted([
            d for d in os.listdir(data_root)
            if os.path.isdir(os.path.join(data_root, d))
        ])

        limit = options.get('limit')
        if limit:
            cell_ids = cell_ids[:limit]
        self.stdout.write(f'找到 {len(cell_ids)} 个格网，开始导入...')

        created = 0
        for i, cell_id in enumerate(cell_ids):
            _, c = H3Cell.objects.get_or_create(
                cell_id=cell_id,
                defaults={'order_index': i}
            )
            if c:
                created += 1

        self.stdout.write(self.style.SUCCESS(f'导入完成：新增 {created} 个，共 {len(cell_ids)} 个格网'))
