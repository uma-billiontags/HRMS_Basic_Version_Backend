# Request email setup

Employee request emails use Django SMTP. Configure these variables in the backend deployment environment (or the backend `.env` for local development):

```env
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=hrms@example.com
EMAIL_HOST_PASSWORD=your-smtp-app-password
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=HRMS <hrms@example.com>
PUBLIC_API_URL=https://your-public-backend.example.com
```

Use an SMTP app password or provider credential, not a personal mailbox password. `DEFAULT_FROM_EMAIL` must be an address the configured provider is allowed to send from. The employee's account email is added as `Reply-To`.

### Multiple Recipients and CC Support
* **To**: Multiple recipient email addresses can be provided (comma-separated, semicolon-separated, or as a list).
* **CC**: Optional CC recipient email addresses can be provided (comma-separated, semicolon-separated, or as a list).
* Recipients in `To` and `Cc` are included on the initial notification, and all `Cc` addresses are kept in the loop when requests are approved or rejected.

Apply the request table migration before starting the backend:

```powershell
python manage.py migrate
```

The request is only saved as sent after the SMTP server accepts the message. Email Approve/Reject links open a confirmation page; the decision is saved only after the recipient confirms, which avoids automated email scanners changing request status.

`PUBLIC_API_URL` must be the backend origin that email recipients can reach. Without it, the link is built from the host used when the request is sent; a local host such as `127.0.0.1:8000` only works on the computer running Django. Restart the backend after changing this setting.
