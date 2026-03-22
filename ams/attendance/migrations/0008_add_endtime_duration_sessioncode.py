from django.db import migrations, models
import django.utils.timezone


def set_end_time(apps, schema_editor):
    AttendanceSession = apps.get_model('attendance', 'AttendanceSession')
    from datetime import timedelta
    for sess in AttendanceSession.objects.all():
        if not sess.end_time:
            base = sess.start_time or django.utils.timezone.now()
            try:
                sess.end_time = base + timedelta(minutes=getattr(sess, 'duration_minutes', 10) or 10)
                sess.save(update_fields=['end_time'])
            except Exception:
                sess.end_time = base + timedelta(minutes=10)
                sess.save(update_fields=['end_time'])


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0007_student_qr_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendancesession',
            name='end_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='attendancesession',
            name='duration_minutes',
            field=models.PositiveSmallIntegerField(default=10),
        ),
        migrations.RunPython(set_end_time, migrations.RunPython.noop),
    ]
