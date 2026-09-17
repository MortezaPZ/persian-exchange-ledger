"""Receipt files are never served from a public media URL."""
from pathlib import Path

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_safe

from accounts.decorators import require_perm
from accounts.permissions import Perm
from ledger.models import VoucherAttachment


@require_perm(Perm.VOUCHER_VIEW)
@require_safe
def receipt_download(request, pk):
    attachment = get_object_or_404(VoucherAttachment, pk=pk)
    if not attachment.file:
        raise Http404("فایل پیوست موجود نیست.")
    try:
        handle = attachment.file.open("rb")
    except FileNotFoundError:
        raise Http404("فایل پیوست موجود نیست.") from None
    response = FileResponse(
        handle, as_attachment=True, filename=Path(attachment.file.name).name,
        content_type="application/octet-stream",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
