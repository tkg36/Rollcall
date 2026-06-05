# RollCall Pipeline — Setup Guide

## What This Pipeline Does

RollCall is an automated headcount reconciliation pipeline. When a trigger email with XLSX report attachments is sent to a monitored address, the pipeline:

1. Receives the email and stores it in AWS
2. Extracts the XLSX attachments and processes the report data
3. Cross-references lookup data and builds a combined output workbook
4. Emails the output workbook back to the original sender automatically

---

## Quick-Start Checklist

- [ ] Fill in `user-settings.yaml`
- [ ] Fill in `.github/deploy-config.yaml`
- [ ] Set the alarm contact email in `.github/CODEOWNERS`
- [ ] Have your AWS team complete the GitHub Actions access setup (Step 2)
- [ ] Run the Deploy workflow (Step 3)
- [ ] Have your DNS team add the SES records for the pipeline subdomain (Step 4)
- [ ] Submit an AWS Support request for SES production access (Step 5)

---

## Step 1 — Fill in Configuration Files

Three files need to be filled in before deploying.

### `user-settings.yaml` (root of repo)

| Setting | What to put here |
|---------|-----------------|
| `CsvBucket` | A globally unique S3 bucket name for parsed email attachments (e.g. `yourorg-rollcall-csv`) |
| `DeptsBucket` | A globally unique S3 bucket name for reference files and output workbooks (e.g. `yourorg-rollcall-data`) |
| `SesBucket` | A globally unique S3 bucket name for raw inbound emails (e.g. `yourorg-rollcall-ses`) |
| `SenderDomain` | A **subdomain** your organization controls — see note below |
| `SenderEmail` | The full send/receive address for the pipeline (e.g. `rollcall@pipeline.yourdomain.com`) |

> **Why a subdomain is required:** AWS SES email receiving works by pointing an MX record at AWS. Using your root domain (e.g. `yourdomain.com`) would redirect all employee email to AWS. Use a dedicated subdomain (e.g. `pipeline.yourdomain.com`) so only mail sent to that address is handled by the pipeline. Your DNS team will need to create this subdomain — see Step 4.

S3 bucket names must be unique across all AWS accounts globally. If a name is taken, the deploy will fail with `BucketAlreadyExists` — choose names specific to your organization.

### `.github/deploy-config.yaml`

| Setting | What to put here |
|---------|-----------------|
| `aws.region` | AWS region to deploy into (e.g. `us-east-1`) — **must be one of `us-east-1`, `us-west-2`, or `eu-west-1`**; SES email receiving is only supported in those three regions |
| `aws.oidcRoleArn` | ARN of the IAM role GitHub Actions will use — your AWS team will provide this (Step 2) |

Stack names under `stacks` can be left as-is unless they conflict with existing CloudFormation stacks in your account.

### `.github/CODEOWNERS`

Replace the placeholder email with the address that should receive CloudWatch alarm notifications if a Lambda error occurs during a pipeline run. This is typically an operations or on-call contact. The same address is also set as the GitHub code owner for the repository, so it will receive pull request review requests.

---

## Step 2 — GitHub Actions AWS Access (AWS Team Task)

> **Who does this:** Your AWS administrator. If your organization uses a ticketing system, open a request before starting the deploy.

The deploy workflow authenticates to AWS using short-lived OIDC tokens — no stored credentials. This requires a one-time setup: an IAM OIDC identity provider and a role the workflow can assume.

Ask your AWS team to:

1. Create an IAM OIDC identity provider for `https://token.actions.githubusercontent.com` (if one doesn't already exist in the account)
2. Create an IAM role trusted by that provider, scoped to this repository (`repo:YOUR_ORG/YOUR_REPO:*`), with `AdministratorAccess` (or equivalent CloudFormation/Lambda/IAM/SES/SQS/SNS/S3 permissions)
3. Provide the role ARN to paste into `.github/deploy-config.yaml`

**References:**
- [GitHub: Configuring OIDC with AWS](https://docs.github.com/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services)
- [AWS: Creating a role for OIDC federation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html)

---

## Step 3 — Run the Deploy

The deploy workflow is triggered manually to prevent unintended deployments.

1. Go to your GitHub repository → **Actions → Deploy**
2. Click **Run workflow** → select `master` → **Run workflow**
3. If your `production` environment has required reviewers, approve the pending deployment when prompted
4. The workflow takes several minutes. Resources deploy in order: IAM roles → S3 buckets → reference data → SNS → SQS → SES rules → Lambda functions → CloudWatch alarms

If a step fails, open the **AWS CloudFormation console**, click the failing stack, and open the **Events** tab — the error will be listed in plain English. Common issues are in the [Troubleshooting](#troubleshooting) section.

### After the deploy completes — confirm the alarm notification email

When the SNS alarm topics are created, AWS automatically sends a confirmation email to the address in `.github/CODEOWNERS`. The recipient must click **Confirm subscription** in that email before CloudWatch alarms will deliver notifications. The email comes from `no-reply@sns.amazonaws.com` with the subject line "AWS Notification - Subscription Confirmation". If it doesn't arrive within a few minutes, check spam.

---

## Step 4 — Add DNS Records for the Pipeline Subdomain (DNS Team Task)

> **Who does this:** Your DNS or network team. After the deploy runs, provide them the record values from the steps below. If your organization uses a ticketing system, open a request with those values included.

AWS cannot add DNS records on your behalf. The pipeline subdomain won't send or receive email until the following records are in place.

**References:**
- [AWS SES: Easy DKIM setup](https://docs.aws.amazon.com/ses/latest/dg/send-email-authentication-dkim-easy.html)
- [AWS SES: MX record for email receiving](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-mx-record.html)

### 4a — DKIM Records (required for sending)

After the deploy runs, AWS generates three CNAME records. To find them:

1. Open the **SES console → Verified identities** → click your subdomain
2. Click the **DKIM** tab
3. Copy all three CNAME records and provide them to your DNS team

They look like:

| Type | Name | Value |
|------|------|-------|
| CNAME | `<token>._domainkey.pipeline.yourdomain.com` | `<token>.dkim.amazonses.com` |
| CNAME | `<token>._domainkey.pipeline.yourdomain.com` | `<token>.dkim.amazonses.com` |
| CNAME | `<token>._domainkey.pipeline.yourdomain.com` | `<token>.dkim.amazonses.com` |

Verification completes automatically within a few hours after the records are added.

### 4b — MX Record (required for receiving)

| Type | Name | Value | Priority |
|------|------|-------|----------|
| MX | `pipeline.yourdomain.com` | `inbound-smtp.REGION.amazonaws.com` | 10 |

Replace `pipeline.yourdomain.com` with your actual subdomain and `REGION` with your deployment region (e.g. `inbound-smtp.us-east-1.amazonaws.com`). The full list of regional endpoints is in the [AWS documentation](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-mx-record.html).

### 4c — SPF Record (recommended)

Prevents pipeline emails from being flagged as spam by recipient mail servers.

| Type | Name | Value |
|------|------|-------|
| TXT | `pipeline.yourdomain.com` | `"v=spf1 include:amazonses.com ~all"` |

---

## Step 5 — Request SES Production Access (AWS Support Request)

> **Who does this:** Your AWS administrator. Open an internal ticket asking your AWS team to submit this on the account's behalf, or have them check whether the account is already in production mode.

New AWS accounts can only send email to verified addresses. The pipeline needs to send to arbitrary recipients, so production access must be requested.

To check if the account is already in production: open **SES console → Account dashboard**. If no sandbox warning appears, skip this step.

If still in sandbox:

1. Open **SES console → Account dashboard → Request production access**
2. Fill in:
   - **Mail type:** Transactional
   - **Use case:** Automated internal reporting — sends processed HR workbooks to known internal recipients. Low volume (single-digit emails per pipeline run).
3. Submit — AWS typically responds within 24 hours

**Reference:** [AWS SES: Request production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html)

---

## Step 6 — Test the Pipeline

Once DNS records have propagated (allow up to 24–48 hours after your DNS team adds them) and SES is out of sandbox:

1. Send an email to the address in `SenderEmail` with the expected XLSX reports attached
2. Within a few minutes, the output workbook should arrive in your inbox from that same address
3. If nothing arrives, check **CloudWatch → Log groups** for:
   - `/aws/lambda/csvParser`
   - `/aws/lambda/rollcall-lambda`
   - `/aws/lambda/ses-emailer-function`

---

## Troubleshooting

**Deploy fails on the IAM stack**
The GitHub Actions role lacks sufficient permissions. Ask your AWS team to verify it has `AdministratorAccess` or equivalent permissions covering CloudFormation, Lambda, IAM, SES, SQS, SNS, and S3.

**Deploy fails with `BucketAlreadyExists`**
The bucket name in `user-settings.yaml` is taken by another AWS account. Choose a more unique name and redeploy.

**Emails arrive at the SES bucket but csvParser doesn't trigger**
Check that the SNS and SQS stacks deployed successfully and that the SES receipt rule set is active. In the SES console, go to **Email receiving → Rule sets** and confirm `DeliverToS3` is listed as active.

**rollcall-lambda fails with `FileNotFoundError`**
The expected XLSX attachments were not found in the CSV bucket. Check csvParser's CloudWatch logs to confirm extraction succeeded. The pipeline expects files with specific name prefixes.

**ses-emailer fails with `MessageRejected`**
The account is still in SES sandbox (Step 5 not complete), or the subdomain is not yet verified (DNS records from Step 4 not yet propagated). Check **SES console → Verified identities** for the subdomain's verification status.

**Alarm emails are not being received**
The SNS subscription confirmation was not completed. Open the **SNS console → Topics**, find `Lambda1_Error_Notif` or `Lambda2_Error_Notif`, click **Subscriptions**, and check the status. If it shows `PendingConfirmation`, use **Request confirmation** to resend the email.

---

## Reference — Pipeline Architecture

```
Trigger email with XLSX attachments
sent to SenderEmail address
           │
           ▼
    SES Receipt Rule
           │
    ┌──────┴──────┐
    │             │
    ▼             ▼
SesBucket     SNS: emailReceived
(raw email)        │
              SQS: CSV-Parser-queue
                   │
             csvParser Lambda
          (extracts XLSX attachments)
                   │
             CsvBucket (XLSX files)
                   │
             SNS: dataReceived
                   │
             SQS: rollcall-sqs
                   │
           rollcall-lambda
    (processes reports, builds workbook)
                   │
    DeptsBucket / {sender} / output.xlsx
                   │
           S3 event notification
                   │
           ses-emailer Lambda
                   │
    SES → output workbook emailed to sender
```
