from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from . import services
from .decorators import require_perm
from .forms import ChangeOwnPasswordForm, LoginForm, RoleForm, UserForm
from .models import AuditLog, Role, User
from .permissions import Perm


def login_view(request):
    if request.user.is_authenticated:
        return redirect("ledger:dashboard")

    form = LoginForm(request, data=request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            user = form.get_user()
            auth_login(request, user)
            services.log(
                AuditLog.Action.LOGIN,
                summary=f"ورود کاربر {user.get_username()}",
                model_name="User",
                object_id=user.pk,
                user=user,
            )
            return redirect(request.GET.get("next") or "ledger:dashboard")
        services.log(
            AuditLog.Action.LOGIN_FAILED,
            summary=f"تلاش ناموفق برای ورود با نام کاربری «{request.POST.get('username', '')[:60]}»",
            model_name="User",
        )
    return render(request, "accounts/login.html", {"form": form})


@login_required
def logout_view(request):
    if request.method == "POST":
        services.log(
            AuditLog.Action.LOGOUT,
            summary=f"خروج کاربر {request.user.get_username()}",
            model_name="User",
            object_id=request.user.pk,
        )
        auth_logout(request)
        messages.success(request, "از سیستم خارج شدید.")
    return redirect("accounts:login")


@login_required
def profile_view(request):
    form = ChangeOwnPasswordForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        request.user.set_password(form.cleaned_data["new_password1"])
        request.user.must_change_password = False
        request.user.save(update_fields=["password", "must_change_password"])
        update_session_auth_hash(request, request.user)
        services.log(
            AuditLog.Action.UPDATE,
            summary="تغییر رمز عبور توسط خود کاربر",
            model_name="User",
            object_id=request.user.pk,
        )
        messages.success(request, "رمز عبور شما تغییر کرد.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})


# --------------------------------------------------------------------------
# مدیریت کاربران
# --------------------------------------------------------------------------
@require_perm(Perm.USER_MANAGE)
def user_list(request):
    users = User.objects.select_related("role").order_by("-is_active", "username")
    return render(request, "accounts/user_list.html", {"users": users})


@require_perm(Perm.USER_MANAGE)
def user_edit(request, pk=None):
    instance = get_object_or_404(User, pk=pk) if pk else None
    form = UserForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        is_new = instance is None
        before = None if is_new else {
            "username": instance.username,
            "full_name": instance.full_name,
            "role": instance.role.title if instance.role_id else None,
            "is_active": instance.is_active,
        }
        user = form.save()
        services.log(
            AuditLog.Action.CREATE if is_new else AuditLog.Action.UPDATE,
            summary=("تعریف کاربر جدید " if is_new else "ویرایش کاربر ") + user.get_username(),
            model_name="User",
            object_id=user.pk,
            before=before,
            after={
                "username": user.username,
                "full_name": user.full_name,
                "role": user.role.title if user.role_id else None,
                "is_active": user.is_active,
            },
        )
        messages.success(request, "کاربر با موفقیت ذخیره شد.")
        return redirect("accounts:user_list")
    return render(request, "accounts/user_form.html", {"form": form, "instance": instance})


# --------------------------------------------------------------------------
# مدیریت نقش‌ها
# --------------------------------------------------------------------------
@require_perm(Perm.USER_MANAGE)
def role_list(request):
    roles = Role.objects.prefetch_related("permissions").order_by("id")
    return render(request, "accounts/role_list.html", {"roles": roles})


@require_perm(Perm.USER_MANAGE)
def role_edit(request, pk=None):
    instance = get_object_or_404(Role, pk=pk) if pk else None
    form = RoleForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        is_new = instance is None
        before = None if is_new else {"permissions": sorted(instance.permission_codes())}
        role = form.save()
        services.log(
            AuditLog.Action.CREATE if is_new else AuditLog.Action.UPDATE,
            summary=("تعریف نقش " if is_new else "تغییر مجوزهای نقش ") + role.title,
            model_name="Role",
            object_id=role.pk,
            before=before,
            after={"permissions": sorted(role.permission_codes())},
        )
        messages.success(request, "نقش با موفقیت ذخیره شد.")
        return redirect("accounts:role_list")
    return render(request, "accounts/role_form.html", {"form": form, "instance": instance})


# --------------------------------------------------------------------------
# تاریخچه تغییرات
# --------------------------------------------------------------------------
@require_perm(Perm.AUDIT_VIEW)
def audit_list(request):
    qs = AuditLog.objects.select_related("user")
    q = (request.GET.get("q") or "").strip()
    action = (request.GET.get("action") or "").strip()
    if q:
        qs = qs.filter(
            Q(summary__icontains=q)
            | Q(username_snapshot__icontains=q)
            | Q(object_id__icontains=q)
        )
    if action:
        qs = qs.filter(action=action)

    page = Paginator(qs, 60).get_page(request.GET.get("page"))
    return render(
        request,
        "accounts/audit_list.html",
        {"page": page, "q": q, "action": action, "actions": AuditLog.Action.choices},
    )
