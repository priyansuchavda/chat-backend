from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import DeviceToken
from .firebase_service import send_multicast_notification


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ['user', 'device_name', 'token_preview', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['user__username', 'device_name', 'token']
    readonly_fields = ['token', 'created_at', 'updated_at']
    actions = ['send_notification_to_selected', 'send_notification_to_all']

    def token_preview(self, obj):
        if not obj.token:
            return '-'
        if len(obj.token) <= 24:
            return obj.token
        return f"{obj.token[:12]}...{obj.token[-12:]}"

    token_preview.short_description = 'FCM Token'

    def send_notification_to_selected(self, request, queryset):
        tokens = list(queryset.filter(is_active=True).values_list('token', flat=True))
        if not tokens:
            self.message_user(request, "No active devices selected.", level='WARNING')
            return

        result = send_multicast_notification(
            tokens,
            title='ChitChat',
            body='You have a new notification',
            data={'type': 'admin_notification'},
        )
        self.message_user(
            request,
            f"Sent to {len(tokens)} device(s) — Success: {result['success']}, Failed: {result['failure']}",
        )

    send_notification_to_selected.short_description = "Send notification to selected devices"

    def send_notification_to_all(self, request, queryset):
        tokens = list(DeviceToken.objects.filter(is_active=True).values_list('token', flat=True))
        if not tokens:
            self.message_user(request, "No active devices registered.", level='WARNING')
            return

        result = send_multicast_notification(
            tokens,
            title='ChitChat',
            body='You have a new notification',
            data={'type': 'admin_notification'},
        )
        self.message_user(
            request,
            f"Sent to all {len(tokens)} device(s) — Success: {result['success']}, Failed: {result['failure']}",
        )

    send_notification_to_all.short_description = "Send notification to ALL devices"


class DeviceTokenInline(admin.TabularInline):
    model = DeviceToken
    extra = 0
    readonly_fields = ['token', 'device_name', 'is_active', 'created_at']
    can_delete = True
    fields = ['device_name', 'token', 'is_active', 'created_at']


class UserAdmin(BaseUserAdmin):
    inlines = list(BaseUserAdmin.inlines or []) + [DeviceTokenInline]
    list_display = BaseUserAdmin.list_display + ('device_count',)

    def device_count(self, obj):
        return obj.device_tokens.filter(is_active=True).count()

    device_count.short_description = 'Active devices'


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
