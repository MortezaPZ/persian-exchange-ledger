from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import Permission, Role, User


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="نام کاربری",
        widget=forms.TextInput(attrs={"class": "input", "autofocus": True, "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="رمز عبور",
        widget=forms.PasswordInput(attrs={"class": "input", "autocomplete": "current-password"}),
    )

    error_messages = {
        "invalid_login": "نام کاربری یا رمز عبور درست نیست.",
        "inactive": "این حساب کاربری غیرفعال شده است.",
    }


class UserForm(forms.ModelForm):
    password1 = forms.CharField(
        label="رمز عبور",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "input", "autocomplete": "new-password"}),
        help_text="برای کاربر جدید الزامی است. هنگام ویرایش، خالی بگذارید تا رمز فعلی تغییر نکند.",
    )
    password2 = forms.CharField(
        label="تکرار رمز عبور",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "input", "autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ["username", "full_name", "phone", "role", "is_active"]
        labels = {
            "username": "نام کاربری",
            "full_name": "نام و نام خانوادگی",
            "phone": "شماره تماس",
            "role": "نقش",
            "is_active": "فعال",
        }
        widgets = {
            "username": forms.TextInput(attrs={"class": "input", "autocomplete": "off"}),
            "full_name": forms.TextInput(attrs={"class": "input"}),
            "phone": forms.TextInput(attrs={"class": "input", "dir": "ltr"}),
            "role": forms.Select(attrs={"class": "input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = Role.objects.all()
        self.fields["role"].required = True

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1") or ""
        p2 = cleaned.get("password2") or ""
        if p1 or p2:
            if p1 != p2:
                self.add_error("password2", "دو رمز عبور یکسان نیستند.")
            elif len(p1) < 8:
                self.add_error("password1", "رمز عبور باید حداقل ۸ کاراکتر باشد.")
        elif self.instance.pk is None:
            self.add_error("password1", "برای کاربر جدید وارد کردن رمز عبور الزامی است.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password1")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user


class RoleForm(forms.ModelForm):
    class Meta:
        model = Role
        fields = ["code", "title", "description", "permissions"]
        labels = {
            "code": "کد نقش",
            "title": "عنوان نقش",
            "description": "توضیح",
            "permissions": "مجوزها",
        }
        widgets = {
            "code": forms.TextInput(attrs={"class": "input", "dir": "ltr"}),
            "title": forms.TextInput(attrs={"class": "input"}),
            "description": forms.TextInput(attrs={"class": "input"}),
            "permissions": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["permissions"].queryset = Permission.objects.all()
        if self.instance.pk and self.instance.is_system:
            self.fields["code"].disabled = True


class ChangeOwnPasswordForm(forms.Form):
    current_password = forms.CharField(
        label="رمز فعلی", widget=forms.PasswordInput(attrs={"class": "input"})
    )
    new_password1 = forms.CharField(
        label="رمز جدید", widget=forms.PasswordInput(attrs={"class": "input"})
    )
    new_password2 = forms.CharField(
        label="تکرار رمز جدید", widget=forms.PasswordInput(attrs={"class": "input"})
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        value = self.cleaned_data["current_password"]
        if not self.user.check_password(value):
            raise forms.ValidationError("رمز فعلی درست نیست.")
        return value

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("new_password1"), cleaned.get("new_password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("new_password2", "دو رمز یکسان نیستند.")
            elif len(p1) < 8:
                self.add_error("new_password1", "رمز عبور باید حداقل ۸ کاراکتر باشد.")
        return cleaned
