from django.urls import path

from tracker import views

app_name = "tracker"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("transacciones/", views.TransactionListView.as_view(), name="transaction_list"),
    path("transactions/<int:pk>/", views.TransactionDetailView.as_view(), name="transaction_detail"),
    path("importar/", views.ImportTransactionsView.as_view(), name="import"),
    path("importar/conectar/", views.GmailOAuthStartView.as_view(), name="gmail_connect"),
    path("import/callback/", views.GmailOAuthCallbackView.as_view(), name="gmail_callback"),
    path("tarjetas/", views.CardListView.as_view(), name="cards"),
    path("tarjetas/<int:pk>/editar/", views.CardUpdateView.as_view(), name="card_edit"),
]
