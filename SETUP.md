# RollCall Pipeline — Setup Guide

## What This Pipeline Does

RollCall is an automated headcount reconciliation pipeline. When a trigger email with XLSX report attachments is sent to a monitored address, the pipeline:

1. Receives the email and stores it in AWS
2. Extracts the XLSX attachments and processes the report data
3. Cross-references lookup data and builds a combined output workbook
4. Emails the output workbook back to the original sender automatically

Setup involves three areas: filling in configuration files, one-time AWS account preparation, and DNS changes to activate email sending and receiving. The sections below walk through each in order.

> **Note for existing AWS accounts:** If your organization already uses AWS, most of the infrastructure steps are additive — they create new resources without touching existing ones. The main exception is DNS: adding an MX record for SES email receiving will redirect inbound mail for that domain (or subdomain) to AWS. Coordinate with your network/DNS team before making that change.

---

## Quick-Start Checklist

- [ ] Fill in `user-settings.yaml`
- [ ] Fill in `.github/deploy-config.yaml`
- [ ] Have your AWS administrator complete the GitHub Actions OIDC setup (Step 2)
- [ ] Create a `production` environment in GitHub repository settings (Step 3)
- [ ] Run the Deploy workflow (Step 4)
- [ ] Have your DNS/network team add SES DNS records (Step 5)
- [ ] Submit an AWS Support request for SES production access (Step 6)

---

## Step 1 — Fill in Configuration Files

Two files in the repository need to be filled in before deploying. Both use placeholder values that make it obvious what to replace.

### `user-settings.yaml` (root of repo)

| Setting | What to put here |
|---------|-----------------|
| `CsvBucket` | A name for the S3 bucket that will hold parsed email attachments. Must be globally unique across all of AWS (e.g. `yourorg-rollcall-csv`). |
| `DeptsBucket` | A name for the S3 bucket that will hold reference files and output workbooks. Must be globally unique (e.g. `yourorg-rollcall-data`). |
| `SesBucket` | A name for the S3 bucket that will hold raw inbound emails. Must be globally unique (e.g. `yourorg-rollcall-email`). |
| `SenderDomain` | The domain that will send and receive pipeline emails (e.g. `example.com`). Your organization must control this domain's DNS. |
| `SenderEmail` | The full email address the pipeline sends from (e.g. `rollcall@example.com`). Must be an address within `SenderDomain`. |

> **On S3 bucket naming:** Bucket names must be unique across every AWS account in the world. If a name is taken, the S3 stack deploy will fail with a `BucketAlreadyExists` error. Choose names specific to your organization to avoid collisions.

### `.github/deploy-config.yaml`

| Setting | What to put here |
|---------|-----------------|
| `aws.region` | The AWS region to deploy into (e.g. `us-east-1`). All resources will be created in this region. |
| `aws.oidcRoleArn` | The ARN of the IAM role GitHub Actions will use to deploy. Your AWS administrator will provide this after completing Step 2. |

Stack names under `stacks` can be left as-is unless they conflict with existing CloudFormation stacks in your account.

---

## Step 2 — GitHub Actions AWS Access (AWS Administrator Task)

> **Who does this:** Your AWS administrator or cloud team. This is a one-time setup task. If your organization uses a ticketing system, open a request describing what's needed before starting the deploy.

The deploy workflow authenticates to AWS using a short-lived token rather than stored credentials. This requires a one-time setup in the AWS account: creating a trust relationship between GitHub and AWS (called OIDC), then creating a role that the workflow can assume.

**References:**
- [GitHub: Configuring OIDC with Amazon Web Services](https://docs.github.com/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services)
- [AWS: Creating an IAM OIDC identity provider](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html)
- [AWS: Creating a role for OIDC federation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html)

### 2a — Create the OIDC Identity Provider

In the AWS IAM console:

1. Go to **IAM → Identity providers → Add provider**
2. Select **OpenID Connect**
3. Provider URL: `https://token.actions.githubusercontent.com`
4. Click **Get thumbprint**
5. Audience: `sts.amazonaws.com`
6. Click **Add provider**

> This only needs to be done once per AWS account. If `token.actions.githubusercontent.com` already appears in the Identity providers list, skip this step.

### 2b — Create the IAM Role

1. Go to **IAM → Roles → Create role**
2. Trusted entity type: **Web identity**
3. Identity provider: `token.actions.githubusercontent.com`
4. Audience: `sts.amazonaws.com`
5. Add a condition to restrict the role to the RollCall repository:
   - Key: `token.actions.githubusercontent.com:sub`
   - Condition: `StringLike`
   - Value: `repo:YOUR_GITHUB_ORG/YOUR_REPO_NAME:*`
6. Attach permissions. The workflow needs to create and update CloudFormation stacks, Lambda functions, S3 buckets, IAM roles, SQS queues, SNS topics, SES rules, and CloudWatch alarms. `AdministratorAccess` covers all of this and is simplest for initial setup — your security team can scope it down later if needed.
7. Name the role (e.g. `GitHubActions-RollCall`), create it, and copy the role ARN
8. Paste the role ARN into `.github/deploy-config.yaml` under `oidcRoleArn`

---

## Step 3 — Create a GitHub Deployment Environment

The deploy workflow uses a GitHub environment called `production`. This allows you to require manual approval before any deploy runs.

1. Go to your GitHub repository → **Settings → Environments → New environment**
2. Name it `production`
3. Optionally, add required reviewers who must approve deploys before they run

---

## Step 4 — Run the Deploy

1. Go to your GitHub repository → **Actions → Deploy**
2. Click **Run workflow**, select the `master` branch, and click **Run workflow**
3. The workflow will take several minutes. Resources deploy in this order:
   - IAM roles → S3 buckets → Reference data upload → SNS → SQS → SES rules → Lambda functions → CloudWatch alarms → SES rule set activation

If a step fails, check the AWS CloudFormation console in your account — click the failing stack name and open the **Events** tab for a plain-English error message. Common issues are listed in the [Troubleshooting](#troubleshooting) section at the bottom of this guide.

---

## Step 5 — Add DNS Records for SES (DNS/Network Team Task)

> **Who does this:** Your DNS or network team. This step requires access to your domain's DNS records. If your organization uses a ticketing system, open a request including the record values from the steps below after the deploy has run.

The deploy creates an SES identity for your domain, but AWS cannot add DNS records on your behalf. Email will not send or receive until the records below are in place.

**References:**
- [AWS SES: Creating and verifying identities](https://docs.aws.amazon.com/ses/latest/dg/creating-identities.html)
- [AWS SES: Easy DKIM setup](https://docs.aws.amazon.com/ses/latest/dg/send-email-authentication-dkim-easy.html)
- [AWS SES: MX record for email receiving](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-mx-record.html)

### 5a — DKIM Records (required for sending)

After the deploy runs, AWS generates three DKIM CNAME records that must be added to the domain's DNS. To find them:

1. Open the **SES console** → **Verified identities** → click your domain
2. Click the **DKIM** tab
3. You will see three CNAME records to add. They look like:

   | Type | Name | Value |
   |------|------|-------|
   | CNAME | `<token>._domainkey.yourdomain.com` | `<token>.dkim.amazonses.com` |
   | CNAME | `<token>._domainkey.yourdomain.com` | `<token>.dkim.amazonses.com` |
   | CNAME | `<token>._domainkey.yourdomain.com` | `<token>.dkim.amazonses.com` |

4. Provide all three records to your DNS team and ask them to add them
5. Verification typically completes within a few hours of the records being added; the SES console will update the status automatically

### 5b — MX Record (required for receiving)

An MX record tells the internet where to deliver email for your domain. Add:

| Type | Name | Value | Priority |
|------|------|-------|----------|
| MX | `yourdomain.com` | `inbound-smtp.REGION.amazonaws.com` | 10 |

Replace `REGION` with your deployment region (e.g. `inbound-smtp.us-east-1.amazonaws.com`). The full list of regional endpoints is in the [AWS documentation](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-mx-record.html).

> **Important:** If the domain already has an MX record (e.g. pointing to Microsoft 365 or Google Workspace), adding this record will redirect **all** inbound mail for the domain to AWS. To avoid this, use a dedicated subdomain for the pipeline (e.g. `pipeline.yourdomain.com`) and update `SenderDomain` in `user-settings.yaml` to match before deploying.

### 5c — SPF Record (recommended)

An SPF record tells receiving mail servers that SES is authorized to send on behalf of your domain, reducing the chance of pipeline emails being flagged as spam.

| Type | Name | Value |
|------|------|-------|
| TXT | `yourdomain.com` | `"v=spf1 include:amazonses.com ~all"` |

If an SPF record already exists for the domain, ask your DNS team to add `include:amazonses.com` to it rather than creating a new one.

---

## Step 6 — Request SES Production Access (AWS Support Request)

> **Who does this:** Your AWS administrator or the account owner. This requires submitting a request through the AWS console. If your organization uses a ticketing system, open an internal request asking your AWS admin team to submit this on the account's behalf.

New AWS accounts restrict outbound email to verified addresses only (called "sandbox mode"). The pipeline needs to send to arbitrary addresses, so production access must be requested.

**Reference:** [AWS SES: Request production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html)

1. Open the **SES console → Account dashboard**
2. Under **Sending limits**, click **Request production access**
3. Fill in the form:
   - **Mail type:** Transactional
   - **Website URL:** Your organization's internal portal URL, or a brief description if none applies
   - **Use case:** Automated internal reporting — the pipeline sends processed HR workbooks to known recipients within the organization. Volume is low (single-digit emails per pipeline run).
4. Submit the request. AWS typically responds within 24 hours for clearly described business use cases

> If the account is already in production (i.e., it has been used for other SES sending), this step can be skipped. Check **SES console → Account dashboard** — if no sandbox warning is shown, you're already in production.

---

## Step 7 — Test the Pipeline

Once the DNS records have propagated (allow up to 24–48 hours) and SES is out of sandbox:

1. Send an email to the address set as `SenderEmail` in `user-settings.yaml`, with the expected XLSX reports attached
2. Within a few minutes, the output workbook should arrive in your inbox from that same sender address
3. If nothing arrives, check **CloudWatch → Log groups** in the AWS console for:
   - `/aws/lambda/csvParser`
   - `/aws/lambda/rollcall-lambda`
   - `/aws/lambda/ses-emailer-function`

Each log group will show what each Lambda processed and any errors encountered.

---

## Troubleshooting

**Deploy fails on the IAM stack**
The GitHub Actions role does not have sufficient permissions. Ask your AWS administrator to verify the role has `AdministratorAccess` or equivalent CloudFormation and IAM permissions.

**Deploy fails with `BucketAlreadyExists`**
The bucket name chosen in `user-settings.yaml` is already taken by another AWS account. Choose a more unique name and redeploy.

**Emails arrive at the SES bucket but csvParser doesn't trigger**
Confirm the SNS and SQS stacks deployed successfully and that the SES receipt rule is active. In the SES console, go to **Email receiving → Rule sets** and confirm `DeliverToS3` is listed as the active rule set.

**rollcall-lambda fails with `FileNotFoundError`**
The expected XLSX attachments were not found. Check csvParser's CloudWatch logs to confirm attachment extraction succeeded. The pipeline expects files whose names begin with specific prefixes (`ES&F_GR&S Unfilled Requisition Report`, `NEW-IT Contractor-VG-Vendor Req Report-Open`, etc.).

**ses-emailer fails with `MessageRejected`**
Either the account is still in SES sandbox mode (Step 6 not yet complete), or the sender domain has not been verified (DNS records from Step 5 not yet propagated or not yet added). Check the **SES console → Verified identities** for the domain's current verification status.

---

## Reference — Pipeline Architecture

```
Trigger email with XLSX attachments
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
