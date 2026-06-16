from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.index, name='index'),
    path('api/cells/', views.cell_list, name='cell_list'),
    path('api/cells/<str:cell_id>/', views.cell_detail, name='cell_detail'),
    path('api/image/<str:cell_id>/<str:prefix>/<str:year>/', views.serve_image, name='serve_image'),
    path('api/hover/', views.hover_preview, name='hover_preview'),
    path('api/segment/', views.segment, name='segment'),
    path('api/save/', views.save_annotation, name='save_annotation'),
    path('api/gif/<str:cell_id>/', views.serve_gif, name='serve_gif'),
    path('api/stats/', views.stats, name='stats'),
    path('api/georef/<str:cell_id>/<str:prefix>/<str:year>/', views.georef, name='georef'),
]
