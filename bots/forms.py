from django import forms

from .models import BotConfig

INPUT = {"class": "input"}


class BotConfigForm(forms.ModelForm):
    class Meta:
        model = BotConfig
        fields = ["is_enabled", "token", "phone_number_id", "api_base", "webhook_secret"]
        labels = {
            "is_enabled": "ربات فعال باشد",
            "token": "توکن ربات",
            "phone_number_id": "شناسه شماره فرستنده",
            "api_base": "آدرس پایه سرویس",
            "webhook_secret": "کلید امنیتی وب‌هوک",
        }
        widgets = {
            "is_enabled": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "token": forms.TextInput(attrs={**INPUT, "dir": "ltr",
                                            "autocomplete": "off",
                                            "placeholder": "123456789:AAE..."}),
            "phone_number_id": forms.TextInput(attrs={**INPUT, "dir": "ltr"}),
            "api_base": forms.TextInput(attrs={**INPUT, "dir": "ltr"}),
            "webhook_secret": forms.TextInput(attrs={**INPUT, "dir": "ltr",
                                                     "autocomplete": "off"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        platform = self.instance.platform

        if platform == BotConfig.Platform.TELEGRAM:
            self.fields["token"].help_text = (
                "در تلگرام به @BotFather پیام بدهید، دستور /newbot را بزنید و "
                "توکنی که می‌دهد را اینجا بگذارید."
            )
            # این دو فیلد فقط برای واتس‌اپ معنی دارند
            del self.fields["phone_number_id"]
            del self.fields["webhook_secret"]
        else:
            self.fields["token"].help_text = "توکن دسترسی دائمی سرویس‌دهنده واتس‌اپ"
            self.fields["phone_number_id"].help_text = (
                "شناسه شماره‌ای که پیام‌ها از آن ارسال می‌شود (Phone Number ID)"
            )
            self.fields["webhook_secret"].help_text = (
                "همان مقداری که در تنظیمات وب‌هوک سرویس‌دهنده به عنوان Verify Token گذاشته‌اید"
            )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_enabled") and not cleaned.get("token"):
            self.add_error("token", "برای فعال کردن ربات، توکن الزامی است.")
        if (
            cleaned.get("is_enabled")
            and self.instance.platform == BotConfig.Platform.WHATSAPP
            and not cleaned.get("phone_number_id")
        ):
            self.add_error("phone_number_id", "برای واتس‌اپ، شناسه شماره فرستنده الزامی است.")
        return cleaned
