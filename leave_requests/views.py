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


def _serialize(item):
    return {
        "id": item.pk,
        "request_type": item.request_type,
        "recipient_email": item.recipient_email,
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
    recipient = str(data.get("recipient_email", "")).strip()
    subject = str(data.get("subject", "")).strip()
    body = str(data.get("body", "")).strip()
    if request_type not in EmployeeRequest.RequestType.values:
        return Response({"detail": "Choose a valid request template."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        validate_email(recipient)
    except ValidationError:
        return Response({"detail": "Enter a valid recipient email."}, status=status.HTTP_400_BAD_REQUEST)
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

    try:
        with transaction.atomic():
            item = EmployeeRequest.objects.create(
                employee=request.user.instance,
                request_type=request_type,
                recipient_email=recipient,
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
            html = f"""
                <div style='font-family:Arial,sans-serif;max-width:640px;margin:auto;color:#172554'>
                  <h2>Request from {safe_name}</h2><h3>{safe_subject}</h3>
                  {request_details_html}
                  <p>{safe_body}</p>
                  <p style='color:#64748b'>Reply to: {escape(request.user.email)}</p>
                  <a href='{approve_url}' style='display:inline-block;padding:12px 18px;margin-right:8px;background:#059669;color:white;text-decoration:none;border-radius:6px'>Approve</a>
                  <a href='{reject_url}' style='display:inline-block;padding:12px 18px;background:#dc2626;color:white;text-decoration:none;border-radius:6px'>Reject</a>
                  <p style='font-size:12px;color:#64748b'>You will be asked to confirm your decision before it is recorded.</p>
                </div>
            """
            text = (
                f"Request from {request.user.name}\n\n{subject}\n"
                f"Dates: {date_text}\nTime: {time_text}\n\n{body}\n\n"
                f"Reply to: {request.user.email}\n\nApprove: {approve_url}\nReject: {reject_url}"
            )
            email = EmailMultiAlternatives(
                subject=f"HRMS Request: {subject}", body=text,
                from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient],
                reply_to=[request.user.email],
            )
            email.attach_alternative(html, "text/html")
            if email.send(fail_silently=False) != 1:
                raise RuntimeError("The email provider did not accept the message.")
    except Exception as exc:
        return Response({"detail": f"Request email could not be sent: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

    return Response(_serialize(item), status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_requests(request):
    if request.user.role != "admin":
        return Response({"detail": "Admin access only."}, status=status.HTTP_403_FORBIDDEN)
    rows = EmployeeRequest.objects.select_related("employee")
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
            <div style='background:#FEF2F2;border:1px solid #FCA5A5;border-radius:8px;padding:14px;margin:16px 0;'>
                <strong style='color:#DC2626;display:block;margin-bottom:6px;'>Reason for Rejection:</strong>
                <div style='color:#7F1D1D;'>{safe_note}</div>
            </div>
        """
        plain_note = f"\n\nReason for Rejection:\n{decision_note}"
    else:
        status_title = "Request Approved"
        status_color = "#059669"
        note_block = f"""
            <div style='background:#F0FDF4;border:1px solid #86EFAC;border-radius:8px;padding:14px;margin:16px 0;'>
                <strong style='color:#059669;display:block;margin-bottom:6px;'>Decision Note:</strong>
                <div style='color:#14532D;'>{safe_note if safe_note else "Your request has been approved."}</div>
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

    try:
        email = EmailMultiAlternatives(
            subject=f"HRMS Request {decision.capitalize()}: {item.subject}",
            body=plain_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
        )
        email.attach_alternative(html, "text/html")
        email.send(fail_silently=True)
    except Exception:
        pass


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def admin_decide_request(request, pk):
    if request.user.role != "admin":
        return Response({"detail": "Admin access only."}, status=status.HTTP_403_FORBIDDEN)
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
        item.status = decision
        item.decision_note = decision_note
        item.decided_at = timezone.now()
        item.save(update_fields=["status", "decision_note", "decided_at"])

    _send_decision_email(item, decision, decision_note)
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
        return render(request, "leave_requests/decision_result.html", {"result": f"Request {decision} successfully.", "success": True})
    if request.method != "GET":
        return HttpResponse(status=405)
    item = get_object_or_404(EmployeeRequest, decision_token=token)
    if item.status != EmployeeRequest.Status.PENDING:
        return render(request, "leave_requests/decision_result.html", {"result": f"This request was already {item.status}.", "success": False})
    return render(request, "leave_requests/decision_confirm.html", {"item": item, "decision": decision})
