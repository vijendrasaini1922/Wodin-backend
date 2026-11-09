from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.orders.tests import ProjectBase

from .models import SupportTicket


class SupportTests(ProjectBase):
    def setUp(self):
        super().setUp()
        self.staff = APIClient()
        self.staff.force_authenticate(User.objects.create_user("+919000000004", role=Role.SUPPORT))

    def open_ticket(self, **kw):
        body = {"category": "damaged", "subject": "Book arrived damaged", "message": "The cover is torn.", **kw}
        r = self.cust.post("/api/support/tickets/", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.data

    def test_customer_creates_and_replies(self):
        t = self.open_ticket()
        self.assertEqual(t["number"], 1001)
        self.assertEqual(t["status"], "open")
        self.assertEqual(len(t["messages"]), 1)
        r = self.cust.post(f"/api/support/tickets/{t['id']}/reply/", {"body": "Photos attached"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.open_ticket(subject="Second")["number"], 1002)
        self.assertEqual(len(self.cust.get("/api/support/tickets/").data), 2)

    def test_cannot_attach_someone_elses_order(self):
        from apps.orders.models import Order
        pid = self.make_project(submit=False)
        order = Order.objects.get(pk=self.cust.post(f"/api/projects/{pid}/submit/").data["order_id"])
        self.open_ticket(order=str(order.id))
        other = APIClient(); other.force_authenticate(self.other)
        r = other.post("/api/support/tickets/", {"category": "question", "subject": "x", "message": "hello there", "order": str(order.id)}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_isolation(self):
        t = self.open_ticket()
        other = APIClient(); other.force_authenticate(self.other)
        self.assertEqual(other.get(f"/api/support/tickets/{t['id']}/").status_code, 404)
        self.assertEqual(other.post(f"/api/support/tickets/{t['id']}/reply/", {"body": "hi there"}, format="json").status_code, 404)
        self.assertEqual(self.cust.get("/api/ops/tickets/").status_code, 403)

    def test_staff_workflow_internal_notes_hidden_from_customer(self):
        t = self.open_ticket()
        self.staff.post(f"/api/ops/tickets/{t['id']}/reply/", {"body": "Sorry! Sending a replacement."}, format="json")
        self.staff.post(f"/api/ops/tickets/{t['id']}/reply/", {"body": "Check courier claim first", "internal": True}, format="json")
        mine = self.cust.get(f"/api/support/tickets/{t['id']}/").data
        self.assertEqual([m["body"] for m in mine["messages"]], ["The cover is torn.", "Sorry! Sending a replacement."])
        staff_view = self.staff.get(f"/api/ops/tickets/{t['id']}/").data
        self.assertEqual(len(staff_view["messages"]), 3)
        self.assertEqual(staff_view["status"], "in_progress")
        r = self.staff.patch(f"/api/ops/tickets/{t['id']}/", {"status": "resolved", "resolution": "Replacement sent"}, format="json")
        self.assertEqual(r.data["status"], "resolved")
        # customer reply reopens; closed tickets cannot be replied to
        self.cust.post(f"/api/support/tickets/{t['id']}/reply/", {"body": "Still waiting"}, format="json")
        self.assertEqual(SupportTicket.objects.get().status, "open")
        self.staff.patch(f"/api/ops/tickets/{t['id']}/", {"status": "closed"}, format="json")
        self.assertEqual(self.cust.post(f"/api/support/tickets/{t['id']}/reply/", {"body": "hello again"}, format="json").status_code, 400)
        self.assertEqual(self.staff.get("/api/ops/tickets/?status=closed").data["count"], 1)

    def test_damaged_book_scenario_is_counted_as_a_complaint_and_resolved(self):
        """Launch acceptance: the damaged-book scenario works end to end."""
        from apps.adminapi import metrics
        t = self.open_ticket(category="damaged")
        self.assertEqual(metrics.dashboard()["support"], {"open_tickets": 1, "complaints": 1})
        self.staff.post(f"/api/ops/tickets/{t['id']}/reply/", {"body": "Sorry, we will reprint it."}, format="json")
        r = self.staff.patch(f"/api/ops/tickets/{t['id']}/", {"status": "resolved", "resolution": "Reprint sent"}, format="json")
        self.assertEqual(r.data["resolution"], "Reprint sent")
        self.assertEqual(metrics.dashboard()["support"], {"open_tickets": 0, "complaints": 0})

    def test_staff_cannot_rewrite_customer_fields(self):
        t = self.open_ticket()
        self.staff.patch(f"/api/ops/tickets/{t['id']}/", {"subject": "hacked", "user": str(self.other.id)}, format="json")
        ticket = SupportTicket.objects.get()
        self.assertEqual((ticket.subject, ticket.user), ("Book arrived damaged", self.customer))

    def test_public_contact_form(self):
        c = APIClient()
        self.assertEqual(c.post("/api/support/contact/", {"name": "", "message": "short"}, format="json").status_code, 400)
        r = c.post("/api/support/contact/", {"name": "Ravi", "phone": "9876543210", "message": "Do you ship to Goa?"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        t = SupportTicket.objects.get(number=r.data["ticket_number"])
        self.assertIsNone(t.user)
        self.assertEqual(t.messages.count(), 1)
