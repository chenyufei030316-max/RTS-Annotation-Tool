from django.db import models


class H3Cell(models.Model):
    cell_id = models.CharField(max_length=50, unique=True)
    order_index = models.IntegerField(db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order_index']

    def __str__(self):
        return self.cell_id


class Annotation(models.Model):
    cell = models.ForeignKey(H3Cell, on_delete=models.CASCADE, related_name='annotations')
    email = models.CharField(max_length=255, default='', db_index=True)
    year = models.IntegerField()
    polygon = models.JSONField(null=True, blank=True)

    class Meta:
        unique_together = ('cell', 'email', 'year')

    def __str__(self):
        return f"{self.cell.cell_id} - {self.email} - {self.year}"


class CellResult(models.Model):
    cell = models.ForeignKey(H3Cell, on_delete=models.CASCADE, related_name='user_results')
    email = models.CharField(max_length=255, db_index=True)
    result = models.CharField(max_length=10, default='', blank=True)
    status = models.CharField(max_length=20, default='pending')
    note = models.TextField(default='', blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('cell', 'email')

    def __str__(self):
        return f"{self.cell.cell_id} - {self.email} - {self.result}"
