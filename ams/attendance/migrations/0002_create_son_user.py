from django.conf import settings
from django.db import migrations


def create_son_user(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    if not User.objects.filter(username='son').exists():
        # create_user will hash the password properly
        User.objects.create_user(username='son', password='student1111')


class Migration(migrations.Migration):
    dependencies = [
        ('attendance', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(create_son_user),
    ]
