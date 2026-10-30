import re
from datetime import date
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import EmployeeRequest
from tasks.notification_utils import send_notification
from tasks.models import Notification
from accounts.models import Admin


def _parse_and_validate_emails(raw_val, field_name="recipient email", required=False):
    """
    Parses email inputs from either a list of strings, or a string delimited by
    commas, semicolons, or whitespace. Validates format and deduplicates.
    """
    if raw_val is None:
        tokens = []
    elif isinstance(raw_val, str):
        tokens = [p.strip() for p in re.split(r"[,;\s]+", raw_val) if p.strip()]
    elif isinstance(raw_val, (list, tuple)):
        tokens = []
        for item in raw_val:
            if isinstance(item, str):
                for sub in re.split(r"[,;\s]+", item):
                    if sub.strip():
                        tokens.append(sub.strip())
    else:
        tokens = []

    if required and not tokens:
        raise ValidationError(f"Please enter at least one valid {field_name}.")

    cleaned_list = []
    for email in tokens:
        try:
            validate_email(email)
            if email not in cleaned_list:
                cleaned_list.append(email)
        except ValidationError:
            raise ValidationError(f"Invalid {field_name}: '{email}'. Please check the format.")

    if required and not cleaned_list:
        raise ValidationError(f"Please enter at least one valid {field_name}.")

    return cleaned_list


def _is_tl(request):
    return (
        getattr(request.user, "role", None) == "employee"
        and getattr(request.user.instance, "role", "") in ("TL", "Team Lead")
    )


def _serialize(item):
    decided_by_str = ""
    if getattr(item, "decided_by_admin", None):
        decided_by_str = f"Admin ({item.decided_by_admin.name})"
    elif getattr(item, "decided_by_employee", None):
        decided_by_str = f"TL ({item.decided_by_employee.name})"

    return {
        "id": item.pk,
        "request_type": item.request_type,
        "recipient_email": item.recipient_email,
        "cc_email": getattr(item, "cc_email", "") or "",
        "sender_email": item.employee.email,
        "employee_name": item.employee.name,
        "subject": item.subject,
        "body": item.body,
        "start_date": item.start_date,
        "end_date": item.end_date,
        "start_time": item.start_time,
        "end_time": item.end_time,
        "status": item.status,
        "decision_note": item.decision_note,
        "decided_by_name": decided_by_str,
        "created_at": item.created_at,
        "decided_at": item.decided_at,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_requests(request):
    if request.user.role != "employee":
        return Response({"detail": "Employee access only."}, status=status.HTTP_403_FORBIDDEN)
    rows = EmployeeRequest.objects.filter(employee=request.user.instance).select_related("employee")
    return Response([_serialize(item) for item in rows])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_request(request):
    if request.user.role != "employee":
        return Response({"detail": "Employee access only."}, status=status.HTTP_403_FORBIDDEN)

    data = request.data
    request_type = data.get("request_type")
    raw_recipient = data.get("recipient_email") or data.get("recipient_emails") or data.get("to")
    raw_cc = data.get("cc_email") or data.get("cc_emails") or data.get("cc")
    subject = str(data.get("subject", "")).strip()
    body = str(data.get("body", "")).strip()

    if request_type not in EmployeeRequest.RequestType.values:
        return Response({"detail": "Choose a valid request template."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        to_list = _parse_and_validate_emails(raw_recipient, field_name="recipient email", required=True)
        cc_list = _parse_and_validate_emails(raw_cc, field_name="CC email", required=False)
    except ValidationError as err:
        return Response({"detail": err.message if hasattr(err, "message") else str(err)}, status=status.HTTP_400_BAD_REQUEST)

    if not subject or len(subject) > 200 or not body:
        return Response({"detail": "Subject (up to 200 characters) and message are required."}, status=status.HTTP_400_BAD_REQUEST)

    def parse_date(field):
        value = data.get(field)
        if not value:
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            raise ValueError(f"Enter a valid {field.replace('_', ' ')}.")

    try:
        start_date = parse_date("start_date")
        end_date = parse_date("end_date")
        if request_type == "leave" and (not start_date or not end_date or end_date < start_date):
            return Response({"detail": "Enter a valid leave start and end date."}, status=status.HTTP_400_BAD_REQUEST)
        if request_type == "permission" and (not start_date or not data.get("start_time") or not data.get("end_time")):
            return Response({"detail": "Enter the permission date, start time, and end time."}, status=status.HTTP_400_BAD_REQUEST)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    if not settings.EMAIL_HOST:
        return Response(
            {"detail": "Email delivery is not configured. Set EMAIL_HOST and the SMTP credentials in the backend environment."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    recipient_str = ", ".join(to_list)
    cc_str = ", ".join(cc_list)

    try:
        with transaction.atomic():
            item = EmployeeRequest.objects.create(
                employee=request.user.instance,
                request_type=request_type,
                recipient_email=recipient_str,
                cc_email=cc_str,
                subject=subject,
                body=body,
                start_date=start_date,
                end_date=end_date,
                start_time=data.get("start_time") or None,
                end_time=data.get("end_time") or None,
            )
            public_api_url = settings.PUBLIC_API_URL or request.build_absolute_uri("/").rstrip("/")
            approve_url = f"{public_api_url}/api/requests/email-decision/{item.decision_token}/approved/"
            reject_url = f"{public_api_url}/api/requests/email-decision/{item.decision_token}/rejected/"
            safe_subject = escape(subject)
            safe_name = escape(request.user.name)
            safe_body = escape(body).replace("\n", "<br>")
            safe_to = escape(recipient_str)
            safe_cc = escape(cc_str)

            date_text = str(start_date) if start_date else ""
            if end_date and end_date != start_date:
                date_text += f" to {end_date}"
            time_text = ""
            if data.get("start_time") and data.get("end_time"):
                time_text = f"{data.get('start_time')} to {data.get('end_time')}"

            request_details_html = ""
            if date_text:
                request_details_html += f"<p><strong>Dates:</strong> {escape(date_text)}</p>"
            if time_text:
                request_details_html += f"<p><strong>Time:</strong> {escape(time_text)}</p>"

            cc_html_line = f"<p><strong>Cc:</strong> {safe_cc}</p>" if safe_cc else ""

            html = f"""
                <div style='font-family:Arial,sans-serif;max-width:640px;margin:auto;color:#172554;padding:24px;border:1px solid #E2E8F0;border-radius:12px;'>
                  <h2 style='margin-top:0;'>Request from {safe_name}</h2>
                  <h3 style='color:#1E293B;'>{safe_subject}</h3>
                  <p><strong>To:</strong> {safe_to}</p>
                  {cc_html_line}
                  {request_details_html}
                  <div style='margin:16px 0;padding:12px 16px;background:#F8FAFC;border-radius:8px;border:1px solid #E2E8F0;line-height:1.6;'>
                    {safe_body}
                  </div>
                  <p style='color:#64748b;font-size:13px;'>Reply to: {escape(request.user.email)}</p>
                  <div style='margin:20px 0;'>
                    <a href='{approve_url}' style='display:inline-block;padding:12px 20px;margin-right:10px;background:#059669;color:white;text-decoration:none;border-radius:6px;font-weight:600;'>Approve</a>
                    <a href='{reject_url}' style='display:inline-block;padding:12px 20px;background:#dc2626;color:white;text-decoration:none;border-radius:6px;font-weight:600;'>Reject</a>
                  </div>
                  <p style='font-size:12px;color:#64748b;margin-bottom:0;'>You will be asked to confirm your decision before it is recorded.</p>
                </div>
            """
            cc_plain = f"Cc: {cc_str}\n" if cc_str else ""
            text = (
                f"Request from {request.user.name}\n\n{subject}\n"
                f"To: {recipient_str}\n{cc_plain}"
                f"Dates: {date_text}\nTime: {time_text}\n\n{body}\n\n"
                f"Reply to: {request.user.email}\n\nApprove: {approve_url}\nReject: {reject_url}"
            )
            email = EmailMultiAlternatives(
                subject=f"HRMS Request: {subject}",
                body=text,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=to_list,
                cc=cc_list if cc_list else None,
                reply_to=[request.user.email],
            )
            email.attach_alternative(html, "text/html")
            if email.send(fail_silently=False) < 1:
                raise RuntimeError("The email provider did not accept the message.")
    except Exception as exc:
        return Response({"detail": f"Request email could not be sent: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

    # In-app notifications
    sender_name = request.user.name or "Employee"
    req_type = item.get_request_type_display() if hasattr(item, "get_request_type_display") else "Request"
    for adm in Admin.objects.filter(is_active=True):
        send_notification(
            recipient=adm,
            title="New Employee Request",
            message=f"{sender_name} submitted a new {req_type}: '{item.subject}'.",
            event_type=Notification.EventType.REQUEST_CREATED,
            action_url="/admin/requests",
            sender_name=sender_name,
        )

    if hasattr(request.user, "instance") and getattr(request.user.instance, "belong_to_tl", None):
        send_notification(
            recipient=request.user.instance.belong_to_tl,
            title="Team Member Request",
            message=f"{sender_name} submitted a new {req_type}: '{item.subject}'.",
            event_type=Notification.EventType.REQUEST_CREATED,
            action_url="/employee/requests",
            sender_name=sender_name,
        )

    return Response(_serialize(item), status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_requests(request):
    is_admin = getattr(request.user, "role", None) == "admin"
    is_tl = _is_tl(request)
    if not (is_admin or is_tl):
        return Response({"detail": "Admin or Team Lead access only."}, status=status.HTTP_403_FORBIDDEN)

    rows = EmployeeRequest.objects.select_related("employee", "decided_by_admin", "decided_by_employee")
    if is_tl and not is_admin:
        # TL receives requests ONLY from employees who belong to THIS TL
        rows = rows.filter(employee__belong_to_tl=request.user.instance)

    status_filter = request.query_params.get("status")
    if status_filter in EmployeeRequest.Status.values:
        rows = rows.filter(status=status_filter)
    return Response([_serialize(item) for item in rows])


def _send_decision_email(item, decision, decision_note=""):
    recipient = item.employee.email
    if not recipient:
        return
    safe_name = escape(item.employee.name)
    safe_subject = escape(item.subject)
    safe_type = escape(item.get_request_type_display())
    safe_note = escape(decision_note).replace("\n", "<br>")

    date_text = str(item.start_date) if item.start_date else ""
    if item.end_date and item.end_date != item.start_date:
        date_text += f" to {item.end_date}"
    time_text = ""
    if item.start_time and item.end_time:
        time_text = f"{str(item.start_time)[:5]} to {str(item.end_time)[:5]}"

    details_html = ""
    if date_text:
        details_html += f"<p style='margin:4px 0;'><strong>Dates:</strong> {escape(date_text)}</p>"
    if time_text:
        details_html += f"<p style='margin:4px 0;'><strong>Time:</strong> {escape(time_text)}</p>"

    if decision == "rejected":
        status_title = "Request Rejected"
        status_color = "#DC2626"
        note_block = f"""
            <div style='margin-top:16px;padding:12px 16px;background:#FEF2F2;border:1px solid #FCA5A5;border-radius:8px;'>
                <strong style='color:#DC2626;display:block;margin-bottom:4px;'>Reason for Rejection:</strong>
                <span style='color:#991B1B;'>{safe_note}</span>
            </div>
        """ if safe_note else ""
        plain_note = f"\n\nReason for Rejection: {decision_note}" if decision_note else ""
    else:
        status_title = "Request Approved"
        status_color = "#059669"
        note_block = f"""
            <div style='margin-top:16px;padding:12px 16px;background:#F0FDF4;border:1px solid #86EFAC;border-radius:8px;'>
                <strong style='color:#16A34A;display:block;margin-bottom:4px;'>Approval Note:</strong>
                <span style='color:#166534;'>{safe_note}</span>
            </div>
        """ if safe_note else "<div style='color:#059669;font-weight:bold;margin:14px 0;'>Your request has been approved.</div>"
        plain_note = f"\n\nNote: {decision_note}" if decision_note else ""

    html = f"""
        <div style='font-family:Arial,sans-serif;max-width:640px;margin:auto;color:#1E293B;padding:24px;border:1px solid #E2E8F0;border-radius:12px;'>
            <h2 style='color:{status_color};margin-top:0;'>{status_title}</h2>
            <p>Hello <strong>{safe_name}</strong>,</p>
            <p>Your <strong>{safe_type}</strong> request for <strong>"{safe_subject}"</strong> has been <strong style='color:{status_color};'>{decision.upper()}</strong>.</p>
            {details_html}
            {note_block}
            <div style='margin-top:20px;padding-top:14px;border-top:1px solid #E2E8F0;font-size:12px;color:#64748B;'>
                Sent by HRMS Automated Notifications.
            </div>
        </div>
    """

    plain_text = (
        f"Hello {item.employee.name},\n\n"
        f"Your {item.get_request_type_display()} request for \"{item.subject}\" has been {decision.upper()}."
        f"{plain_note}\n\n"
        f"Dates: {date_text}\nTime: {time_text}\n\n"
        "HRMS Notification"
    )

    cc_list = item.get_cc_list()

    try:
        email = EmailMultiAlternatives(
            subject=f"HRMS Request {decision.capitalize()}: {item.subject}",
            body=plain_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            cc=cc_list if cc_list else None,
        )
        email.attach_alternative(html, "text/html")
        email.send(fail_silently=True)
    except Exception:
        pass


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def admin_decide_request(request, pk):
    is_admin = getattr(request.user, "role", None) == "admin"
    is_tl = _is_tl(request)
    if not (is_admin or is_tl):
        return Response({"detail": "Admin or Team Lead access only."}, status=status.HTTP_403_FORBIDDEN)

    decision = request.data.get("decision")
    if decision not in ("approved", "rejected"):
        return Response({"detail": "Decision must be approved or rejected."}, status=status.HTTP_400_BAD_REQUEST)
    
    decision_note = str(request.data.get("decision_note", "")).strip()[:2000]
    if decision == "rejected" and not decision_note:
        return Response({"detail": "A rejection reason is required."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        item = get_object_or_404(EmployeeRequest.objects.select_for_update().select_related("employee"), pk=pk)
        if item.status != EmployeeRequest.Status.PENDING:
            return Response({"detail": "This request has already been decided."}, status=status.HTTP_409_CONFLICT)
        
        if is_tl and not is_admin:
            if item.employee_id == request.user.instance.id:
                return Response({"detail": "You cannot decide on your own request."}, status=status.HTTP_403_FORBIDDEN)
            if item.employee and item.employee.role in ("TL", "Team Lead"):
                return Response({"detail": "Team Lead requests must be decided by an Administrator."}, status=status.HTTP_403_FORBIDDEN)
            if item.employee and item.employee.belong_to_tl_id != request.user.instance.id:
                return Response({"detail": "You can only decide requests for your team members."}, status=status.HTTP_403_FORBIDDEN)
            item.decided_by_employee = request.user.instance
            item.decided_by_admin = None
        elif is_admin:
            item.decided_by_admin = request.user.instance
            item.decided_by_employee = None

        item.status = decision
        item.decision_note = decision_note
        item.decided_at = timezone.now()
        item.save()

    _send_decision_email(item, decision, decision_note)

    # In-app notification to requester
    decider_name = request.user.name if hasattr(request.user, "name") else "Management"
    evt_type = (
        Notification.EventType.REQUEST_REJECTED
        if decision == "rejected"
        else Notification.EventType.REQUEST_APPROVED
    )
    send_notification(
        recipient=item.employee,
        title=f"Request {decision.capitalize()}",
        message=f"Your request '{item.subject}' was {decision} by {decider_name}." + (f" Note: {decision_note}" if decision_note else ""),
        event_type=evt_type,
        action_url="/employee/requests",
        sender_name=decider_name,
    )

    return Response(_serialize(item))


def email_decision(request, token, decision):
    if request.method == "POST":
        if decision not in ("approved", "rejected"):
            return HttpResponse("Invalid decision.", status=400)
        decision_note = str(request.POST.get("decision_note", "")).strip()[:2000]
        if decision == "rejected" and not decision_note:
            item = get_object_or_404(EmployeeRequest, decision_token=token)
            return render(request, "leave_requests/decision_confirm.html", {
                "item": item,
                "decision": decision,
                "error": "A rejection reason is required.",
            })
        with transaction.atomic():
            item = EmployeeRequest.objects.select_for_update().select_related("employee").filter(decision_token=token).first()
            if not item:
                return HttpResponse("This decision link is invalid or expired.", status=404)
            if item.status != EmployeeRequest.Status.PENDING:
                return render(request, "leave_requests/decision_result.html", {"result": f"This request was already {item.status}.", "success": False})
            item.status = decision
            item.decision_note = decision_note
            item.decided_at = timezone.now()
            item.save(update_fields=["status", "decision_note", "decided_at"])

        _send_decision_email(item, decision, decision_note)
        evt_type = (
            Notification.EventType.REQUEST_REJECTED
            if decision == "rejected"
            else Notification.EventType.REQUEST_APPROVED
        )
        send_notification(
            recipient=item.employee,
            title=f"Request {decision.capitalize()}",
            message=f"Your request '{item.subject}' was {decision} via email." + (f" Note: {decision_note}" if decision_note else ""),
            event_type=evt_type,
            action_url="/employee/requests",
            sender_name="Management",
        )
        return render(request, "leave_requests/decision_result.html", {"result": f"Request {decision} successfully.", "success": True})
    if request.method != "GET":
        return HttpResponse(status=405)
    item = get_object_or_404(EmployeeRequest, decision_token=token)
    if item.status != EmployeeRequest.Status.PENDING:
        return render(request, "leave_requests/decision_result.html", {"result": f"This request was already {item.status}.", "success": False})
    return render(request, "leave_requests/decision_confirm.html", {"item": item, "decision": decision})
