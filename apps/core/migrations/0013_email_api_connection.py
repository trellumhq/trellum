from django.db import migrations, models
import django.db.models.deletion

import apps.core.crypto


class Migration(migrations.Migration):
    dependencies = [("core", "0012_instanceconfig_lockout_account_threshold_and_more")]

    operations = [
        migrations.CreateModel(
            name="EmailApiConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("provider", models.CharField(choices=[("sendgrid", "SendGrid"), ("amazon_ses", "Amazon SES"), ("mailgun", "Mailgun"), ("postmark", "Postmark"), ("brevo", "Brevo"), ("resend", "Resend"), ("mailjet", "Mailjet"), ("mailersend", "MailerSend"), ("mailtrap", "Mailtrap Email Sending"), ("custom_https", "Custom HTTPS")], max_length=24)),
                ("credentials", apps.core.crypto.EncryptedJSONField(blank=True, default=dict)),
                ("provider_config", models.JSONField(blank=True, default=dict)),
                ("custom_config", models.JSONField(blank=True, default=dict)),
                ("from_email", models.EmailField(max_length=254)),
                ("config_revision", models.PositiveIntegerField(default=1)),
                ("last_test_results", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddField(
            model_name="instanceconfig",
            name="active_email_api_connection",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="active_instances", to="core.emailapiconnection"),
        ),
    ]
