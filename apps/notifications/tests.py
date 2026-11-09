from django.core import mail
from django.test import override_settings

from apps.orders.models import Order
from apps.orders.tests import ProjectBase

from .audit import log
from .models import AuditLog, Notification


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class NotificationAndAuditTests(ProjectBase):
    def test_audit_log_is_append_only(self):
        entry = log("test.action", actor=self.customer, obj=self.trip, extra="x")
        self.assertEqual((entry.object_type, entry.object_id), ("Trip", str(self.trip.pk)))
        entry.details = {"tampered": True}
        with self.assertRaises(ValueError):
            entry.save()
        with self.assertRaises(ValueError):
            entry.delete()

    def test_workflow_events_are_audited_with_revision_and_identity(self):
        pid = self.make_project(submit=False)
        self.cust.post(f"/api/projects/{pid}/submit/")
        self.layout(pid)
        rev = self.publish(pid)
        self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev["id"]}, format="json")
        approval = AuditLog.objects.get(action="revision.approved")
        self.assertEqual(approval.actor, self.customer)
        self.assertEqual(approval.details["revision"], rev["id"])
        self.assertTrue(AuditLog.objects.filter(action="revision.published", actor=self.designer).exists())
        changes = AuditLog.objects.filter(action="order.status_changed").values_list("details", flat=True)
        self.assertIn("approved", [d["to_status"] for d in changes])

    def test_status_change_sends_sms_and_email_once_committed(self):
        self.customer.email = "asha@example.com"
        self.customer.save()
        pid = self.make_project(submit=False)
        with self.captureOnCommitCallbacks(execute=True):
            self.cust.post(f"/api/projects/{pid}/submit/")
        self.assertEqual(Notification.objects.filter(kind="order.design", channel="sms", status="sent").count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["asha@example.com"])
        self.assertIn("WD-", mail.outbox[0].body)

    def test_no_notification_without_transactional_consent(self):
        from apps.accounts.models import CustomerProfile
        CustomerProfile.objects.create(user=self.customer, transactional_consent=False)  # OTP signup always creates one
        pid = self.make_project(submit=False)
        with self.captureOnCommitCallbacks(execute=True):
            self.cust.post(f"/api/projects/{pid}/submit/")
        self.assertEqual(Notification.objects.exclude(status="skipped").count(), 0)
        self.assertGreaterEqual(Notification.objects.filter(status="skipped").count(), 1)

    def test_delivery_failure_does_not_break_the_order(self):
        self.customer.email = "asha@example.com"
        self.customer.save()
        pid = self.make_project(submit=False)
        with override_settings(EMAIL_BACKEND="nonexistent.backend.Class"):
            with self.captureOnCommitCallbacks(execute=True):
                r = self.cust.post(f"/api/projects/{pid}/submit/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Notification.objects.filter(channel="email", status="failed").count(), 1)
        self.assertEqual(Order.objects.get().status, "design")
